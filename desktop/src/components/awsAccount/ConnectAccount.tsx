/**
 * Connect (or change) the SwarmAI AWS account — wizard steps ② ③ ④ (design
 * docs/design/2026-10-31-swarmai-aws-account.md §6.1, §6.3, §6.3b, §7.5). Shared by the
 * setup wizard and Settings → SwarmAI Account ("Change account", "Sign in again").
 *
 * ② The accounts this machine already reaches are listed at once (stage 1 reads only
 *    the configuration) and verified row by row in the background (stage 2, only the
 *    rows that cannot prompt; the others get a button the person clicks). One decision:
 *    which account. An account ID typed in is the anchor: rows of other accounts are
 *    greyed out.
 * ③ Signing in when nothing usable was found: ADA (Midway expired → "run mwinit -f",
 *    retried every 5 s until it works), AWS SSO in the app (code shown here, browser
 *    opened, polled), AWS login (through the AWS CLI), and "more ways" (a profile,
 *    access keys, the instance role).
 * ④ The backend verifies the candidate with STS before anything changes (a failure
 *    keeps the current account), then the capabilities are shown, one line each.
 *
 * Every screen keeps the same exits: back, another way, chat only, configure later. A
 * change to another account while Hives run in the current one asks first (keep them,
 * stop and delete only; or delete them before changing).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  awsAccountService, problemOf, type AccountProblem, type AwsAccountView, type Candidate,
  type DetectResult, type DetectRow, type SsoAccount, type SsoStarted, type VerifyRowResult,
} from '../../services/awsAccount';
import { systemService } from '../../services/system';
import { settingsService } from '../../services/settings';
import { openExternal } from '../../utils/openExternal';
import { OPEN_SETTINGS_EVENT } from '../common/CredentialBanner';

const ACCOUNT_RE = /^\d{12}$/;
const REGION_RE = /^[a-z]{2}(-[a-z]+)+-\d$/;
const ADA_RETRY_MS = 5000;
const LOGIN_POLL_MS = 3000;
const CLI_INSTALL_URL = 'https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html';

export type ConnectStart = 'pick' | 'ada' | 'sso' | 'login';

export interface ConnectPrefill {
  accountId?: string | null;
  region?: string | null;
  ada?: { role?: string | null; provider?: string | null } | null;
  sso?: { start_url?: string | null; sso_region?: string | null; role?: string | null } | null;
}

export interface ConnectAccountProps {
  mode: 'onboarding' | 'settings';
  /** ④ done: the account is connected (the view after the capability checks) */
  onConnected: (view: AwsAccountView) => void;
  /** a chat key was checked and stored (wizard only: chat works, Hive waits) */
  onChatOnly?: () => void;
  /** "Configure later" (wizard) */
  onLater?: () => void;
  /** back to the settings page */
  onCancel?: () => void;
  start?: ConnectStart;
  prefill?: ConnectPrefill;
}

type Screen =
  | { kind: 'pick' }
  | { kind: 'ada' }
  | { kind: 'ada_wait'; cand: Candidate }
  | { kind: 'sso'; target?: { accountId: string | null; role: string | null } }
  | { kind: 'sso_wait'; flow: SsoStarted; startUrl: string; ssoRegion: string; target?: { accountId: string | null; role: string | null } }
  | { kind: 'sso_pick'; accounts: SsoAccount[]; startUrl: string; ssoRegion: string; target?: { accountId: string | null; role: string | null } }
  | { kind: 'login' }
  | { kind: 'login_wait' }
  | { kind: 'more' }
  | { kind: 'chat' }
  | { kind: 'old_hives'; cand: Candidate; problem: AccountProblem }
  | { kind: 'verified'; view: AwsAccountView };

type RowStatus = VerifyRowResult | { status: 'pending' };

/** Rows with the same account, role and kind of sign-in read as one line ("2 profiles"). */
interface Group { key: string; rows: DetectRow[]; accountId: string | null }

const isAda = (r: DetectRow) => r.kind === 'ada' || r.kind === 'ada_default';

function groupRows(rows: DetectRow[]): Group[] {
  const groups: Group[] = [];
  const byKey = new Map<string, Group>();
  for (const r of rows) {
    const key = r.account_id && (isAda(r) || r.kind === 'sso')
      ? `${r.account_id}|${r.role ?? ''}|${isAda(r) ? 'ada' : r.kind}`
      : `solo|${r.profile}`;
    const g = byKey.get(key);
    if (g) g.rows.push(r);
    else {
      const ng = { key, rows: [r], accountId: r.account_id };
      byKey.set(key, ng);
      groups.push(ng);
    }
  }
  return groups;
}

const inputCls = 'w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]';
const linkCls = 'text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors';
const smallBtn = 'px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-card)] disabled:opacity-50';

export default function ConnectAccount({
  mode, onConnected, onChatOnly, onLater, onCancel, start = 'pick', prefill,
}: ConnectAccountProps) {
  const { t } = useTranslation();
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const [screen, setScreen] = useState<Screen>(
    start === 'sso' ? { kind: 'sso', target: { accountId: prefill?.accountId ?? null, role: prefill?.sso?.role ?? null } }
      : { kind: start } as Screen);
  const [detect, setDetect] = useState<DetectResult | null>(null);
  const [detectFailed, setDetectFailed] = useState(false);
  const [rowStatus, setRowStatus] = useState<Record<string, RowStatus>>({});
  const [picked, setPicked] = useState<string | null>(null);
  const [accountId, setAccountId] = useState(prefill?.accountId ?? '');
  const [region, setRegion] = useState(prefill?.region ?? 'us-east-1');
  const [context, setContext] = useState<'internal' | 'external' | null>(null);
  const [showMethods, setShowMethods] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AccountProblem | null>(null);

  const anchor = ACCOUNT_RE.test(accountId.trim()) ? accountId.trim() : null;
  const regionOk = REGION_RE.test(region.trim());
  const ctx = context ?? detect?.context ?? 'external';

  // ── ② detection: stage 1 at once, stage 2 row by row ─────────────────────
  const [detectRun, setDetectRun] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setDetectFailed(false);
    awsAccountService.detect().then((d) => {
      if (cancelled) return;
      setDetect(d);
      const init: Record<string, RowStatus> = {};
      for (const r of d.rows) {
        init[r.profile] = r.verify === 'auto' ? { status: 'pending' }
          : r.verify === 'check' ? { status: 'check', detail: r.detail ?? undefined }
          : { status: 'needs_login' };
      }
      setRowStatus(init);
      for (const r of d.rows.filter((x) => x.verify === 'auto')) {
        awsAccountService.verifyRow(r.profile)
          .then((res) => { if (!cancelled) setRowStatus((s) => ({ ...s, [r.profile]: res })); })
          .catch(() => { if (!cancelled) setRowStatus((s) => ({ ...s, [r.profile]: { status: 'failed' } })); });
      }
    }).catch(() => { if (!cancelled) setDetectFailed(true); });
    return () => { cancelled = true; };
  }, [detectRun]);

  const groups = useMemo(() => groupRows(detect?.rows ?? []), [detect]);

  const groupAccount = useCallback((g: Group): string | null => {
    if (g.accountId) return g.accountId;
    for (const r of g.rows) {
      const st = rowStatus[r.profile];
      if (st && 'account_id' in st && st.account_id) return st.account_id;
    }
    return null;
  }, [rowStatus]);

  const groupStatus = useCallback((g: Group): RowStatus['status'] => {
    const order: RowStatus['status'][] = ['verified', 'pending', 'check', 'needs_login', 'timeout', 'failed', 'invalid'];
    const sts = g.rows.map((r) => rowStatus[r.profile]?.status ?? 'pending');
    return order.find((o) => sts.includes(o)) ?? 'failed';
  }, [rowStatus]);

  const selectable = useCallback((g: Group): boolean => {
    const st = groupStatus(g);
    if (st !== 'verified' && st !== 'pending') return false;
    const acct = groupAccount(g);
    return !(anchor && acct && acct !== anchor);
  }, [anchor, groupAccount, groupStatus]);

  // the default: ADA's own profile, then an ADA profile of the same account, then the
  // first verified row (only rows already verified; the person can pick any row)
  const defaultKey = useMemo(() => {
    const ok = groups.filter((g) => selectable(g) && groupStatus(g) === 'verified');
    const adaOwn = ok.find((g) => g.rows.some((r) => r.kind === 'ada_default'));
    const ownAcct = groups.find((g) => g.rows.some((r) => r.kind === 'ada_default'))?.accountId;
    return (adaOwn ?? ok.find((g) => isAda(g.rows[0]) && g.accountId === ownAcct) ?? ok[0])?.key ?? null;
  }, [groups, selectable, groupStatus]);
  const selectedKey = picked && groups.some((g) => g.key === picked && selectable(g)) ? picked : defaultKey;
  const selected = groups.find((g) => g.key === selectedKey) ?? null;

  // ── connect (④): the backend verifies first; a failure keeps the current account ──
  const connect = useCallback(async (cand: Candidate, keep?: 'keep', quiet = false): Promise<boolean> => {
    if (!quiet) { setBusy(true); setError(null); }
    try {
      const v = await awsAccountService.connect(cand, keep);
      if (mounted.current) setScreen({ kind: 'verified', view: v });
      return true;
    } catch (e) {
      const p = problemOf(e);
      if (!mounted.current) return false;
      if (p.kind === 'old_hives') setScreen({ kind: 'old_hives', cand, problem: p });
      else if (p.kind === 'needs_login' && cand.source === 'ada') {
        if (!quiet) setScreen({ kind: 'ada_wait', cand });   // a retry: keep waiting
      } else {
        setError(p);
        if (cand.source === 'ada') {
          // a role, provider or account the person can correct: the ADA form, filled in
          setAccountId(cand.account_id ?? '');
          if (cand.ada) { setAdaRole(cand.ada.role); setAdaProvider(cand.ada.provider); }
          setScreen({ kind: 'ada' });
        } else if (cand.source === 'login') {
          setScreen({ kind: 'login' });   // never left on the waiting screen
        }
      }
      return false;
    } finally {
      if (!quiet && mounted.current) setBusy(false);
    }
  }, []);

  const base = (source: Candidate['source']): Candidate => ({
    source, region: region.trim(), context: ctx, ...(anchor ? { account_id: anchor } : {}),
  });

  const chooseGroup = (g: Group) => {
    const r = g.rows[0];
    const acct = groupAccount(g);
    if (isAda(r) && r.ada) {
      void connect({ ...base('ada'), account_id: acct ?? undefined, ada: r.ada });
    } else if (r.kind === 'sso') {
      startSsoFor(r);
    } else if (r.kind === 'login') {
      setScreen({ kind: 'login' });
    } else {
      void connect({ ...base('profile'), profile: r.profile, ...(acct ? { account_id: anchor ?? acct } : {}) });
    }
  };

  // ── ADA ───────────────────────────────────────────────────────────────────
  const [adaRole, setAdaRole] = useState(prefill?.ada?.role ?? 'Admin');
  const [adaProvider, setAdaProvider] = useState(prefill?.ada?.provider ?? 'isengard');
  useEffect(() => {
    if (screen.kind !== 'ada_wait') return undefined;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (stop) return;
      const done = await connect(screen.cand, undefined, true);
      if (!stop && !done && mounted.current) timer = setTimeout(loop, ADA_RETRY_MS);
    };
    timer = setTimeout(loop, ADA_RETRY_MS);
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [screen, connect]);

  // ── AWS SSO (device sign-in in the app) ───────────────────────────────────
  const firstSso = detect?.rows.find((r) => r.kind === 'sso' && r.sso?.start_url)?.sso ?? null;
  const [ssoUrl, setSsoUrl] = useState(prefill?.sso?.start_url ?? '');
  const [ssoRegion, setSsoRegion] = useState(prefill?.sso?.sso_region ?? '');
  const ssoUrlValue = ssoUrl || firstSso?.start_url || '';
  const ssoRegionValue = ssoRegion || firstSso?.sso_region || 'us-east-1';

  const ssoBegin = async (url: string, sregion: string, target?: { accountId: string | null; role: string | null }) => {
    setBusy(true); setError(null);
    try {
      const flow = await awsAccountService.ssoStart(url.trim(), sregion.trim());
      if (!mounted.current) return;
      setScreen({ kind: 'sso_wait', flow, startUrl: url.trim(), ssoRegion: sregion.trim(), target });
      void openExternal(flow.verification_url).catch(() => { /* the link is shown to copy */ });
    } catch (e) {
      if (mounted.current) setError(problemOf(e));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const startSsoFor = (r: DetectRow) => {
    const target = { accountId: r.account_id, role: r.role };
    if (r.sso?.start_url && r.sso.sso_region) void ssoBegin(r.sso.start_url, r.sso.sso_region, target);
    else setScreen({ kind: 'sso', target });
  };

  useEffect(() => {
    if (screen.kind !== 'sso_wait') return undefined;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const { flow, startUrl, ssoRegion: sreg, target } = screen;
    const poll = async () => {
      if (stop) return;
      try {
        const res = await awsAccountService.ssoPoll(flow.flow_id);
        if (stop || !mounted.current) return;
        if (res.status === 'done') {
          setScreen({ kind: 'sso_pick', accounts: res.accounts, startUrl, ssoRegion: sreg, target });
          return;
        }
        timer = setTimeout(poll, Math.max(1, flow.interval) * 1000);
      } catch (e) {
        if (!stop && mounted.current) { setError(problemOf(e)); setScreen({ kind: 'sso', target }); }
      }
    };
    timer = setTimeout(poll, Math.max(1, flow.interval) * 1000);
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [screen]);

  // ── AWS login (through the AWS CLI) ───────────────────────────────────────
  const loginBegin = async () => {
    setBusy(true); setError(null);
    try {
      await awsAccountService.loginStart(region.trim());
      if (mounted.current) setScreen({ kind: 'login_wait' });
    } catch (e) {
      if (mounted.current) setError(problemOf(e));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  useEffect(() => {
    if (screen.kind !== 'login_wait') return undefined;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      if (stop) return;
      try {
        const st = await awsAccountService.loginStatus();
        if (stop || !mounted.current) return;
        if (st.status === 'done') {
          await connect({ ...base('login'), login: { profile: st.profile ?? '' } });
          return;
        }
        if (st.status === 'failed' || st.status === 'idle') {
          setError({ kind: 'login_failed', message: st.detail ?? t('awsConnect.login.failed') });
          setScreen({ kind: 'login' });
          return;
        }
      } catch (e) {
        if (!stop && mounted.current) { setError(problemOf(e)); setScreen({ kind: 'login' }); }
        return;
      }
      timer = setTimeout(poll, LOGIN_POLL_MS);
    };
    timer = setTimeout(poll, LOGIN_POLL_MS);
    return () => { stop = true; if (timer) clearTimeout(timer); };
    // base() reads the current region and anchor; the poll starts once per sign-in
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, connect]);

  // ── shared bits ───────────────────────────────────────────────────────────
  const go = (s: Screen) => { setError(null); setScreen(s); };
  const exits = (opts: { back?: boolean; another?: boolean } = {}) => (
    <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2" data-testid="connect-exits">
      {opts.back && <button type="button" className={linkCls} onClick={() => go({ kind: 'pick' })}>{t('awsConnect.back')}</button>}
      {opts.another && <button type="button" className={linkCls} onClick={() => { setShowMethods(true); go({ kind: 'pick' }); }}>{t('awsConnect.another')}</button>}
      {mode === 'onboarding' && onChatOnly && (
        <button type="button" className={linkCls} onClick={() => go({ kind: 'chat' })}>{t('awsConnect.chatOnly')}</button>)}
      {mode === 'onboarding' && onLater && (
        <button type="button" className={linkCls} onClick={onLater}>{t('awsConnect.later')}</button>)}
      {mode === 'settings' && onCancel && (
        <button type="button" className={linkCls} onClick={onCancel}>{t('awsConnect.cancel')}</button>)}
    </div>
  );
  const errorBox = error && (
    <div data-testid="connect-error" className="mt-4 p-3 rounded-md border border-[var(--color-error)]/40 text-sm">
      <p className="text-[var(--color-error)] font-medium">
        {t(`awsConnect.error.${error.kind}`, { defaultValue: t('awsConnect.error.generic'), expected: error.expected, actual: error.actual })}
      </p>
      {error.message && <p className="mt-1 text-[var(--color-text-secondary)] break-words">{error.message}</p>}
    </div>
  );
  const regionField = (
    <label className="block">
      <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.region')}</span>
      <input value={region} onChange={(e) => setRegion(e.target.value)} className={inputCls} data-testid="connect-region"
        aria-invalid={!regionOk} />
      {!regionOk && <span className="block mt-1 text-xs text-[var(--color-warning)]" data-testid="connect-region-hint">{t('awsConnect.regionShape')}</span>}
    </label>
  );
  const title = mode === 'settings' ? t('awsConnect.titleChange') : t('awsConnect.title');

  // ── screens ───────────────────────────────────────────────────────────────
  if (screen.kind === 'verified') {
    return <Verified view={screen.view} mode={mode} onDone={onConnected} onChat={onChatOnly ? () => go({ kind: 'chat' }) : undefined} />;
  }

  if (screen.kind === 'old_hives') {
    const p = screen.problem;
    return (
      <div data-testid="connect-old-hives">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.oldHives.title', { account: p.account_id })}</h2>
        <p className="text-[var(--color-text-muted)] mb-4">{t('awsConnect.oldHives.lead')}</p>
        <ul className="mb-4 space-y-1 text-sm">
          {(p.hives ?? []).map((h) => (
            <li key={h.id} className="text-[var(--color-text)]">{h.name} <span className="text-[var(--color-text-muted)]">· {h.status}</span></li>
          ))}
        </ul>
        {p.can_keep ? (
          <>
            <p className="text-sm text-[var(--color-text-secondary)] mb-4">{t('awsConnect.oldHives.keepMeans')}</p>
            <div className="flex gap-3">
              <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={busy}
                onClick={() => void connect(screen.cand, 'keep')} data-testid="connect-keep-hives">
                {busy ? t('awsConnect.verifying') : t('awsConnect.oldHives.keep')}
              </button>
            </div>
          </>
        ) : (
          <p className="text-sm text-[var(--color-text-secondary)]">{t('awsConnect.oldHives.cannotKeep')}</p>
        )}
        <p className="mt-3 text-sm text-[var(--color-text-muted)]">
          {t('awsConnect.oldHives.deleteFirst')}{' '}
          {mode === 'settings' && (
            <button type="button" className="underline" data-testid="connect-open-hive-page"
              onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'hive' } }))}>
              {t('awsConnect.oldHives.openHivePage')}
            </button>
          )}
        </p>
        {errorBox}
        {exits({ back: true })}
      </div>
    );
  }

  if (screen.kind === 'ada') {
    const ok = ACCOUNT_RE.test(accountId.trim()) && adaRole.trim() && regionOk;
    return (
      <div data-testid="connect-ada">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.ada.title')}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">{t('awsConnect.ada.lead')}</p>
        <div className="grid grid-cols-2 gap-4 max-w-lg">
          <label className="block col-span-2">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.accountId')}</span>
            <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="123456789012"
              className={inputCls} data-testid="connect-ada-account" aria-invalid={!ACCOUNT_RE.test(accountId.trim())} />
            {!ACCOUNT_RE.test(accountId.trim()) && (
              <span className="block mt-1 text-xs text-[var(--color-text-muted)]">{t('awsConnect.accountIdShape')}</span>)}
          </label>
          <label className="block">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.ada.role')}</span>
            <input value={adaRole} onChange={(e) => setAdaRole(e.target.value)} className={inputCls} />
          </label>
          <label className="block">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.ada.provider')}</span>
            <select value={adaProvider} onChange={(e) => setAdaProvider(e.target.value)} className={inputCls}>
              <option value="isengard">Isengard</option>
              <option value="conduit">Conduit</option>
            </select>
          </label>
          <div className="col-span-2">{regionField}</div>
        </div>
        {errorBox}
        <div className="mt-6">
          <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={!ok || busy}
            data-testid="connect-ada-go"
            onClick={() => void connect({ ...base('ada'), account_id: accountId.trim(),
              ada: { role: adaRole.trim(), provider: adaProvider } })}>
            {busy ? t('awsConnect.verifying') : t(error ? 'awsConnect.retry' : 'awsConnect.ada.go')}
          </button>
        </div>
        {exits({ back: true, another: true })}
      </div>
    );
  }

  if (screen.kind === 'ada_wait') {
    const c = screen.cand;
    return (
      <div data-testid="connect-ada-wait">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.signInTo', { account: c.account_id })}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">ADA · {c.ada?.role} · {c.ada?.provider}</p>
        <div className="p-4 rounded-lg bg-[var(--color-card)] flex items-start gap-3">
          <span className="material-symbols-outlined icon-sm animate-spin text-[var(--color-text-muted)]">progress_activity</span>
          <div>
            <p className="font-medium text-[var(--color-text)]">{t('awsConnect.ada.midwayTitle')}</p>
            <p className="text-sm text-[var(--color-text-secondary)] mt-1">
              {t('awsConnect.ada.midwayRun')} <code className="font-mono">mwinit -f</code>{t('awsConnect.ada.midwayThen')}
            </p>
            <p className="text-xs text-[var(--color-text-muted)] mt-2">{t('awsConnect.ada.retrying')}</p>
          </div>
        </div>
        {errorBox}
        {exits({ back: true, another: true })}
      </div>
    );
  }

  if (screen.kind === 'sso') {
    const ok = /^https:\/\//.test(ssoUrlValue.trim()) && REGION_RE.test(ssoRegionValue.trim()) && regionOk;
    return (
      <div data-testid="connect-sso">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.sso.title')}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">{t('awsConnect.sso.lead')}</p>
        <div className="grid grid-cols-2 gap-4 max-w-lg">
          <label className="block col-span-2">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.sso.startUrl')}</span>
            <input value={ssoUrlValue} onChange={(e) => setSsoUrl(e.target.value)}
              placeholder="https://my-company.awsapps.com/start" className={inputCls} data-testid="connect-sso-url" />
            {ssoUrlValue.trim() !== '' && !/^https:\/\//.test(ssoUrlValue.trim()) && (
              <span className="block mt-1 text-xs text-[var(--color-warning)]">{t('awsConnect.sso.urlShape')}</span>)}
          </label>
          <label className="block">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.sso.region')}</span>
            <input value={ssoRegionValue} onChange={(e) => setSsoRegion(e.target.value)} className={inputCls} />
          </label>
          {regionField}
        </div>
        {errorBox}
        <div className="mt-6">
          <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={!ok || busy}
            data-testid="connect-sso-go"
            onClick={() => void ssoBegin(ssoUrlValue, ssoRegionValue, screen.target)}>
            {busy ? t('awsConnect.verifying') : t('awsConnect.sso.go')}
          </button>
        </div>
        {exits({ back: true, another: true })}
      </div>
    );
  }

  if (screen.kind === 'sso_wait') {
    const f = screen.flow;
    return (
      <div data-testid="connect-sso-wait">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.sso.confirmTitle')}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">{t('awsConnect.sso.confirmLead', { url: screen.startUrl })}</p>
        <div className="p-4 rounded-lg bg-[var(--color-card)] flex items-start gap-3">
          <span className="material-symbols-outlined icon-sm animate-spin text-[var(--color-text-muted)]">progress_activity</span>
          <div>
            <p className="font-mono text-2xl tracking-widest text-[var(--color-text)]" data-testid="connect-sso-code">{f.user_code}</p>
            <p className="text-xs text-[var(--color-text-muted)] mt-2">
              {t('awsConnect.sso.noBrowser')}{' '}
              <button type="button" className="underline" onClick={() => void openExternal(f.verification_url)}>{t('awsConnect.sso.openAgain')}</button>
              {' · '}
              <button type="button" className="underline" onClick={() => void navigator.clipboard?.writeText(f.verification_url)}>{t('awsConnect.sso.copyLink')}</button>
              {' · '}{t('awsConnect.sso.validFor', { minutes: Math.round(f.expires_in / 60) })}
            </p>
          </div>
        </div>
        {exits({ back: true, another: true })}
      </div>
    );
  }

  if (screen.kind === 'sso_pick') {
    return <SsoPick screen={screen} anchor={anchor} busy={busy} errorBox={errorBox}
      onPick={(acct, role) => void connect({ ...base('sso'), account_id: acct,
        sso: { start_url: screen.startUrl, sso_region: screen.ssoRegion, role } })}
      exits={exits({ back: true, another: true })} />;
  }

  if (screen.kind === 'login' || screen.kind === 'login_wait') {
    const support = detect?.login;
    const unavailable = support && !support.available;
    return (
      <div data-testid="connect-login">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.login.title')}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">{t('awsConnect.login.lead')}</p>
        {unavailable && (
          <p className="mb-4 text-sm text-[var(--color-warning)]" data-testid="connect-login-cli">
            {t(support?.reason === 'old_cli' ? 'awsConnect.login.oldCli' : 'awsConnect.login.noCli', { version: support?.version ?? '' })}{' '}
            <button type="button" className="underline" onClick={() => void openExternal(CLI_INSTALL_URL)}>{t('awsConnect.login.howUpdate')}</button>
          </p>
        )}
        {screen.kind === 'login_wait' ? (
          <div className="p-4 rounded-lg bg-[var(--color-card)] flex items-center gap-3">
            <span className="material-symbols-outlined icon-sm animate-spin text-[var(--color-text-muted)]">progress_activity</span>
            <span className="text-[var(--color-text-secondary)]">{t('awsConnect.login.waiting')}</span>
          </div>
        ) : (
          <>
            <div className="max-w-xs">{regionField}</div>
            {errorBox}
            <div className="mt-6">
              <button type="button" className="btn-lg btn-primary disabled:opacity-50"
                disabled={!!unavailable || !regionOk || busy} onClick={() => void loginBegin()} data-testid="connect-login-go">
                {busy ? t('awsConnect.verifying') : t('awsConnect.login.go')}
              </button>
            </div>
          </>
        )}
        {exits({ back: true, another: true })}
      </div>
    );
  }

  if (screen.kind === 'more') {
    return <MoreWays rows={detect?.rows ?? []} busy={busy} errorBox={errorBox} regionField={regionField}
      onProfile={(name) => void connect({ ...base('profile'), profile: name })}
      onKeys={async (id, secret) => {
        setBusy(true); setError(null);
        let keysId: string;
        try {
          keysId = (await awsAccountService.storeKeys(id, secret)).keys_id;
        } catch (e) {
          if (mounted.current) { setError(problemOf(e)); setBusy(false); }
          return false;
        }
        setBusy(false);
        return connect({ ...base('keys'), keys_id: keysId });
      }}
      onInstanceRole={() => void connect({ ...base('instance_role') })}
      exits={exits({ back: true })} />;
  }

  if (screen.kind === 'chat') {
    return <ChatOnly region={regionOk ? region.trim() : 'us-east-1'} context={ctx}
      onDone={() => onChatOnly?.()} exits={exits({ back: true })} />;
  }

  // ── ② the pick screen ──────────────────────────────────────────────────────
  const usable = groups.some((g) => selectable(g));
  const methods = ctx === 'internal' ? (['ada', 'sso'] as const) : (['login', 'sso'] as const);
  return (
    <div data-testid="connect-pick">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{title}</h2>
      <p className="text-[var(--color-text-muted)] mb-1">{t('awsConnect.lead')}</p>
      <p className="text-xs text-[var(--color-text-muted)] mb-6">
        {ctx === 'internal' ? t('awsConnect.ctxInternal') : t('awsConnect.ctxExternal')}{' '}
        <button type="button" className="underline" data-testid="connect-ctx-switch"
          onClick={() => setContext(ctx === 'internal' ? 'external' : 'internal')}>{t('awsConnect.ctxSwitch')}</button>
      </p>

      {!detect && !detectFailed && (
        <p className="text-sm text-[var(--color-text-muted)]" data-testid="connect-detecting">{t('awsConnect.detecting')}</p>
      )}
      {detectFailed && (
        <p className="text-sm text-[var(--color-warning)]" data-testid="connect-detect-failed">
          {t('awsConnect.detectFailed')}{' '}
          <button type="button" className="underline" onClick={() => setDetectRun((n) => n + 1)}>{t('awsConnect.retry')}</button>
        </p>
      )}
      {detect && groups.length === 0 && (
        <p className="text-sm text-[var(--color-text-secondary)]" data-testid="connect-nothing-found">{t('awsConnect.nothingFound')}</p>
      )}

      {groups.length > 0 && (
        <>
          <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-2">{t('awsConnect.found')}</h3>
          <ul className="divide-y divide-[var(--color-border)] border-y border-[var(--color-border)]" data-testid="connect-rows"
            role="radiogroup" aria-label={t('awsConnect.found')}>
            {groups.map((g) => (
              <DetectedRow key={g.key} group={g} account={groupAccount(g)} status={groupStatus(g)}
                selected={g.key === selectedKey} selectable={selectable(g)}
                notAnchor={!!(anchor && groupAccount(g) && groupAccount(g) !== anchor)}
                onSelect={() => setPicked(g.key)}
                onCheck={(r) => {
                  setRowStatus((s) => ({ ...s, [r.profile]: { status: 'pending' } }));
                  awsAccountService.checkRow(r.profile)
                    .then((res) => setRowStatus((s) => ({ ...s, [r.profile]: res })))
                    .catch(() => setRowStatus((s) => ({ ...s, [r.profile]: { status: 'failed' } })));
                }}
                onLogin={(r) => (r.kind === 'sso' ? startSsoFor(r) : go({ kind: 'login' }))}
                onRetry={(r) => {
                  setRowStatus((s) => ({ ...s, [r.profile]: { status: 'pending' } }));
                  (r.verify === 'auto' ? awsAccountService.verifyRow(r.profile) : awsAccountService.checkRow(r.profile))
                    .then((res) => setRowStatus((s) => ({ ...s, [r.profile]: res })))
                    .catch(() => setRowStatus((s) => ({ ...s, [r.profile]: { status: 'failed' } })));
                }} />
            ))}
          </ul>
        </>
      )}

      <div className="mt-5 grid grid-cols-[1fr_10rem] gap-4 max-w-lg">
        <label className="block">
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.accountIdOptional')}</span>
          <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="123456789012"
            className={inputCls} data-testid="connect-anchor" />
          <span className="block mt-1 text-xs text-[var(--color-text-muted)]">
            {groups.length > 0 ? t('awsConnect.anchorHint') : t('awsConnect.accountIdWhere')}
          </span>
        </label>
        {regionField}
      </div>
      {anchor === null && accountId.trim() !== '' && (
        <p className="mt-1 text-xs text-[var(--color-warning)]">{t('awsConnect.accountIdShape')}</p>
      )}

      {errorBox}

      {usable && selected && (
        <div className="mt-6 flex items-center gap-4">
          <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={busy || !regionOk}
            data-testid="connect-use" onClick={() => chooseGroup(selected)}>
            {busy ? t('awsConnect.verifying')
              : t('awsConnect.useAccount', { account: groupAccount(selected) ?? selected.rows[0].profile })}
          </button>
          <span className="text-xs text-[var(--color-text-muted)]">{t('awsConnect.useHint')}</span>
        </div>
      )}

      {(!usable || showMethods) && (detect || detectFailed) && (
        <div className="mt-6" data-testid="connect-methods">
          <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-2">{t('awsConnect.how')}</h3>
          <div className="grid grid-cols-2 gap-3">
            {methods.map((m) => (
              <button key={m} type="button" data-testid={`connect-method-${m}`}
                onClick={() => go(m === 'sso' ? { kind: 'sso' } : { kind: m })}
                className="text-left p-4 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-primary)] transition-colors">
                <span className="block font-medium text-[var(--color-text)]">{t(`awsConnect.method.${m}.title`)}</span>
                <span className="block mt-1 text-sm text-[var(--color-text-secondary)]">{t(`awsConnect.method.${m}.desc`)}</span>
                {m === 'login' && detect?.login && !detect.login.available && (
                  <span className="block mt-2 text-xs text-[var(--color-warning)]">
                    {t(detect.login.reason === 'old_cli' ? 'awsConnect.login.oldCli' : 'awsConnect.login.noCli', { version: detect.login.version ?? '' })}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2" data-testid="connect-exits">
        {usable && !showMethods && (
          <button type="button" className={linkCls} onClick={() => setShowMethods(true)}>{t('awsConnect.otherWays')}</button>)}
        <button type="button" className={linkCls} onClick={() => go({ kind: 'more' })} data-testid="connect-more">{t('awsConnect.more')}</button>
        {mode === 'onboarding' && onChatOnly && (
          <button type="button" className={linkCls} onClick={() => go({ kind: 'chat' })} data-testid="connect-chat-only">{t('awsConnect.chatOnly')}</button>)}
        {mode === 'onboarding' && onLater && (
          <button type="button" className={linkCls} onClick={onLater} data-testid="connect-later">{t('awsConnect.later')}</button>)}
        {mode === 'settings' && onCancel && (
          <button type="button" className={linkCls} onClick={onCancel}>{t('awsConnect.cancel')}</button>)}
      </div>
    </div>
  );
}

// ── one detected account ─────────────────────────────────────────────────────

function DetectedRow({ group, account, status, selected, selectable, notAnchor, onSelect, onCheck, onLogin, onRetry }: {
  group: Group; account: string | null; status: RowStatus['status']; selected: boolean; selectable: boolean;
  notAnchor: boolean; onSelect: () => void; onCheck: (r: DetectRow) => void; onLogin: (r: DetectRow) => void;
  onRetry: (r: DetectRow) => void;
}) {
  const { t } = useTranslation();
  const r = group.rows[0];
  const names = group.rows.map((x) => x.profile || t('awsConnect.adaOwn'));
  const via = isAda(r) ? `ADA · ${r.ada?.provider === 'conduit' ? 'Conduit' : 'Isengard'}`
    : t(`awsConnect.kind.${r.kind}`);
  const where = group.rows.length > 1
    ? t('awsConnect.nProfiles', { n: group.rows.length, names: names.join(', ') })
    : r.kind === 'ada_default' ? t('awsConnect.adaOwn') : t('awsConnect.profileNamed', { name: r.profile });
  return (
    <li className={`flex items-center gap-3 py-2.5 ${selectable ? 'cursor-pointer' : 'opacity-60'}`}
      data-testid="connect-row" data-account={account ?? ''} onClick={() => selectable && onSelect()}
      role="radio" aria-checked={selected} aria-disabled={!selectable} tabIndex={selectable ? 0 : -1}
      onKeyDown={(e) => { if (selectable && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); onSelect(); } }}>
      <span className={`w-3.5 h-3.5 shrink-0 rounded-full border ${selected ? 'border-[var(--color-primary)] bg-[var(--color-primary)]' : 'border-[var(--color-border)]'}`} />
      <span className="w-32 shrink-0 font-mono text-sm text-[var(--color-text)]">{account ?? '—'}</span>
      <span className="flex-1 min-w-0 text-sm">
        <span className="text-[var(--color-text)]">{r.role ?? via}</span>
        <span className="block text-xs text-[var(--color-text-muted)] truncate">
          {r.role ? `${via} · ${where}` : where}{notAnchor ? ` · ${t('awsConnect.notAnchor')}` : ''}
        </span>
      </span>
      <span className="shrink-0 text-xs" data-testid="connect-row-status">
        {status === 'check' ? (
          <button type="button" className={smallBtn} onClick={(e) => { e.stopPropagation(); onCheck(r); }}>{t('awsConnect.check')}</button>
        ) : status === 'needs_login' ? (
          <button type="button" className={smallBtn} onClick={(e) => { e.stopPropagation(); onLogin(r); }}>{t('awsConnect.signIn')}</button>
        ) : status === 'failed' || status === 'timeout' ? (
          <span className="inline-flex items-center gap-2">
            <span className="text-[var(--color-error)]">{t(`awsConnect.status.${status}`)}</span>
            <button type="button" className={smallBtn} onClick={(e) => { e.stopPropagation(); onRetry(r); }}>{t('awsConnect.checkAgain')}</button>
          </span>
        ) : (
          <span className={status === 'verified' ? 'text-green-500'
            : status === 'pending' ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-error)]'}>
            {t(`awsConnect.status.${status}`)}
          </span>
        )}
      </span>
    </li>
  );
}

// ── AWS SSO: choose the account and role this sign-in can reach ──────────────

function SsoPick({ screen, anchor, busy, errorBox, onPick, exits }: {
  screen: Extract<Screen, { kind: 'sso_pick' }>; anchor: string | null; busy: boolean;
  errorBox: ReactNode; onPick: (account: string, role: string) => void; exits: ReactNode;
}) {
  const { t } = useTranslation();
  const want = anchor ?? screen.target?.accountId ?? null;
  const visible = want ? screen.accounts.filter((a) => a.account_id === want) : screen.accounts;
  const [acct, setAcct] = useState<string | null>(visible.length === 1 ? visible[0].account_id : null);
  const roles = screen.accounts.find((a) => a.account_id === acct)?.roles ?? [];
  const preferred = screen.target?.role && roles.includes(screen.target.role) ? screen.target.role
    : roles.find((r) => /admin/i.test(r)) ?? roles[0] ?? null;
  const [role, setRole] = useState<string | null>(null);
  const chosenRole = role && roles.includes(role) ? role : preferred;
  return (
    <div data-testid="connect-sso-pick">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.sso.pickTitle')}</h2>
      {want && visible.length === 0 ? (
        <p className="text-[var(--color-warning)] mb-4" data-testid="connect-sso-not-visible">
          {t('awsConnect.sso.notVisible', { account: want })}
        </p>
      ) : null}
      <ul className="divide-y divide-[var(--color-border)] border-y border-[var(--color-border)]" role="radiogroup"
        aria-label={t('awsConnect.sso.pickTitle')}>
        {(visible.length ? visible : screen.accounts).map((a) => (
          <li key={a.account_id} className={`py-2.5 flex items-center gap-3 ${visible.includes(a) ? 'cursor-pointer' : 'opacity-60'}`}
            role="radio" aria-checked={acct === a.account_id} aria-disabled={!visible.includes(a)}
            tabIndex={visible.includes(a) ? 0 : -1}
            onKeyDown={(e) => { if (visible.includes(a) && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); setAcct(a.account_id); } }}
            onClick={() => visible.includes(a) && setAcct(a.account_id)}>
            <span className={`w-3.5 h-3.5 rounded-full border ${acct === a.account_id ? 'border-[var(--color-primary)] bg-[var(--color-primary)]' : 'border-[var(--color-border)]'}`} />
            <span className="font-mono text-sm text-[var(--color-text)]">{a.account_id}</span>
            <span className="text-sm text-[var(--color-text-secondary)]">{a.name}</span>
          </li>
        ))}
      </ul>
      {acct && roles.length > 1 && (
        <label className="block mt-4 max-w-xs">
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.sso.role')}</span>
          <select value={chosenRole ?? ''} onChange={(e) => setRole(e.target.value)} className={inputCls}>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      )}
      {errorBox}
      <div className="mt-6">
        <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={!acct || !chosenRole || busy}
          data-testid="connect-sso-use" onClick={() => acct && chosenRole && onPick(acct, chosenRole)}>
          {busy ? t('awsConnect.verifying') : t('awsConnect.useAccount', { account: acct ?? '' })}
        </button>
      </div>
      {exits}
    </div>
  );
}

// ── more ways: a profile, access keys, the instance role ─────────────────────

function MoreWays({ rows, busy, errorBox, regionField, onProfile, onKeys, onInstanceRole, exits }: {
  rows: DetectRow[]; busy: boolean; errorBox: ReactNode; regionField: ReactNode;
  onProfile: (name: string) => void; onKeys: (id: string, secret: string) => Promise<boolean>;
  onInstanceRole: () => void; exits: ReactNode;
}) {
  const { t } = useTranslation();
  const profiles = rows.filter((r) => r.profile && (r.kind === 'process' || r.kind === 'assume_role' || r.kind === 'keys'));
  const [way, setWay] = useState<'profile' | 'keys' | 'instance_role'>(profiles.length ? 'profile' : 'keys');
  const [profile, setProfile] = useState(profiles[0]?.profile ?? '');
  const [keyId, setKeyId] = useState('');
  const [secret, setSecret] = useState('');
  return (
    <div data-testid="connect-more-ways">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.moreTitle')}</h2>
      <div className="flex gap-2 mb-6">
        {(['profile', 'keys', 'instance_role'] as const).map((w) => (
          <button key={w} type="button" onClick={() => setWay(w)} data-testid={`connect-way-${w}`}
            className={`px-3 py-1.5 text-sm rounded-md border ${way === w ? 'border-[var(--color-primary)] text-[var(--color-text)]' : 'border-[var(--color-border)] text-[var(--color-text-muted)]'}`}>
            {t(`awsConnect.way.${w}.title`)}
          </button>
        ))}
      </div>
      <p className="text-sm text-[var(--color-text-secondary)] mb-4">{t(`awsConnect.way.${way}.desc`)}</p>
      <div className="max-w-lg space-y-4">
        {way === 'profile' && (
          <label className="block">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.way.profile.name')}</span>
            <input value={profile} onChange={(e) => setProfile(e.target.value)} list="connect-profiles"
              className={inputCls} data-testid="connect-profile-name" />
            <datalist id="connect-profiles">{profiles.map((p) => <option key={p.profile} value={p.profile} />)}</datalist>
          </label>
        )}
        {way === 'keys' && (
          <>
            <label className="block">
              <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.way.keys.id')}</span>
              <input value={keyId} onChange={(e) => setKeyId(e.target.value)} placeholder="AKIA…" autoComplete="off"
                className={inputCls} data-testid="connect-key-id" />
            </label>
            <label className="block">
              <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('awsConnect.way.keys.secret')}</span>
              <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off"
                className={inputCls} data-testid="connect-key-secret" />
            </label>
          </>
        )}
        <div className="max-w-xs">{regionField}</div>
      </div>
      {errorBox}
      <div className="mt-6">
        <button type="button" className="btn-lg btn-primary disabled:opacity-50" data-testid="connect-more-go"
          disabled={busy || (way === 'profile' && !profile.trim()) || (way === 'keys' && (!keyId.trim() || !secret))}
          onClick={async () => {
            if (way === 'profile') onProfile(profile.trim());
            else if (way === 'instance_role') onInstanceRole();
            else {
              const ok = await onKeys(keyId.trim(), secret);
              if (ok) setSecret('');   // never kept in memory after it is stored
            }
          }}>
          {busy ? t('awsConnect.verifying') : t('awsConnect.verifyAndUse')}
        </button>
      </div>
      {exits}
    </div>
  );
}

// ── chat only: a Bedrock API key or an Anthropic key ─────────────────────────

function ChatOnly({ region, context, onDone, exits }: {
  region: string; context: 'internal' | 'external'; onDone: () => void; exits: ReactNode;
}) {
  const { t } = useTranslation();
  const [kind, setKind] = useState<'bedrock_api_key' | 'apikey'>('bedrock_api_key');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const run = async () => {
    setBusy(true); setFailed(null);
    // verify first with the not-yet-stored key; store only after it worked
    const update: Record<string, unknown> = kind === 'apikey'
      ? { use_bedrock: false, aws_region: region, auth_method: 'apikey' }
      : { use_bedrock: true, aws_region: region, auth_method: 'bedrock_api_key' };
    const secret = kind === 'apikey' ? { anthropic_api_key: key.trim() } : { aws_bearer_token_bedrock: key.trim() };
    try {
      const res = await systemService.verifyAuth({ ...update, ...secret });
      if (!res.success) {
        setFailed(res.fixHint || res.error || t('awsConnect.chat.failed'));
        return;
      }
      if (kind === 'apikey') await systemService.persistApiKey(key.trim());
      else await systemService.persistBearerToken(key.trim());
      setKey('');
      await settingsService.updateAPIConfiguration(update);
      await systemService.setAuthMethod(kind, context);
      onDone();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div data-testid="connect-chat">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.chat.title')}</h2>
      <p className="text-[var(--color-text-muted)] mb-6">{t('awsConnect.chat.lead')}</p>
      <div className="grid grid-cols-2 gap-3 mb-4">
        {(['bedrock_api_key', 'apikey'] as const).map((k) => (
          <button key={k} type="button" onClick={() => setKind(k)} data-testid={`connect-chat-${k}`}
            className={`text-left p-4 rounded-lg border ${kind === k ? 'border-[var(--color-primary)]' : 'border-[var(--color-border)]'}`}>
            <span className="block font-medium text-[var(--color-text)]">{t(`awsConnect.chat.${k}.title`)}</span>
            <span className="block mt-1 text-sm text-[var(--color-text-secondary)]">{t(`awsConnect.chat.${k}.desc`)}</span>
          </button>
        ))}
      </div>
      <textarea value={key} onChange={(e) => setKey(e.target.value)} rows={3} autoComplete="off"
        placeholder={t('awsConnect.chat.paste')} className={inputCls} data-testid="connect-chat-key" />
      {failed && <p className="mt-3 text-sm text-[var(--color-error)]" data-testid="connect-chat-failed">{failed}</p>}
      <div className="mt-6 flex items-center gap-4">
        <button type="button" className="btn-lg btn-primary disabled:opacity-50" disabled={!key.trim() || busy}
          onClick={() => void run()} data-testid="connect-chat-go">
          {busy ? t('awsConnect.verifying') : t('awsConnect.chat.go')}
        </button>
        <span className="text-xs text-[var(--color-text-muted)]">{t('awsConnect.chat.stored')}</span>
      </div>
      {exits}
    </div>
  );
}

// ── ④ verified: the account and its capabilities ─────────────────────────────

const CAPS = ['bedrock', 'hive', 'storage', 'voice'] as const;

function Verified({ view, mode, onDone, onChat }: {
  view: AwsAccountView; mode: 'onboarding' | 'settings'; onDone: (v: AwsAccountView) => void; onChat?: () => void;
}) {
  const { t } = useTranslation();
  const [v, setV] = useState(view);
  const [busy, setBusy] = useState(false);
  const [recheckFailed, setRecheckFailed] = useState<string | null>(null);
  const caps = v.capabilities;
  // chat on a key does not use this account's Bedrock: nothing to fix here
  const bedrockOk = caps?.bedrock?.status === 'ok' || caps?.bedrock?.status === 'not_applicable';
  const recheck = async () => {
    setBusy(true); setRecheckFailed(null);
    try { setV(await awsAccountService.check()); } catch (e) {
      setRecheckFailed(problemOf(e).message);   // the old result stays, and says why it did not change
    } finally { setBusy(false); }
  };
  return (
    <div data-testid="connect-verified">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('awsConnect.verified.title')}</h2>
      <p className="text-[var(--color-text)] mb-6">
        <span className="font-mono">{v.account?.account_id}</span>
        <span className="text-[var(--color-text-secondary)]"> · {v.account?.caller_arn}</span>
      </p>
      <ul className="space-y-2 mb-6" data-testid="connect-caps">
        {CAPS.map((c) => {
          const cap = caps?.[c];
          const st = cap?.status ?? 'unknown';
          return (
            <li key={c} className="text-sm">
              <span className="inline-block w-28 text-[var(--color-text)]">{t(`settings.awsAccount.cap.${c}`)}</span>
              <span className={st === 'ok' ? 'text-green-500' : st === 'not_applicable' ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-warning)]'}>
                {t(`settings.awsAccount.capStatus.${st}`)}
              </span>
              {st !== 'ok' && cap?.detail && <span className="block text-xs text-[var(--color-text-muted)] break-words">{cap.detail}</span>}
            </li>
          );
        })}
      </ul>
      {!bedrockOk && (
        <p className="text-sm text-[var(--color-text-secondary)] mb-4" data-testid="connect-bedrock-gap">{t('awsConnect.verified.bedrockGap')}</p>
      )}
      {recheckFailed && <p className="text-sm text-[var(--color-error)] mb-4" data-testid="connect-recheck-failed">{recheckFailed}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-lg btn-primary" onClick={() => onDone(v)} data-testid="connect-continue">
          {mode === 'settings' ? t('awsConnect.verified.done') : bedrockOk ? t('awsConnect.verified.continue') : t('awsConnect.verified.continueAnyway')}
        </button>
        {!bedrockOk && (
          <button type="button" className={smallBtn} disabled={busy} onClick={() => void recheck()}>
            {busy ? t('awsConnect.verifying') : t('awsConnect.verified.recheck')}
          </button>
        )}
        {!bedrockOk && onChat && mode === 'onboarding' && (
          <button type="button" className={smallBtn} onClick={onChat}>{t('awsConnect.chatOnly')}</button>
        )}
      </div>
    </div>
  );
}
