/**
 * How many Hives the SwarmAI account runs (design
 * docs/design/2026-10-31-swarmai-aws-account.md §6.1 ⑤, §6.4, §6.5, §7.3).
 *
 * - `HiveSetupStep` — wizard step ⑤ "where tasks run": Elastic Workers (AgentCore Runtime,
 *   on by default; switched off it is recorded with PUT /hive/elastic/standby before the
 *   wizard goes on, and a failure keeps the step with the reason) and resident
 *   Hives (EC2): count 1–8 (default 1), type, region; the button
 *   names what it does ("Create 1 Hive, about $119/month, in account …"); creating is
 *   started, never waited for; when the "can it create" check fails for the chosen
 *   region the step says what is missing and the wizard goes on.
 * The rest of the Hive setup lives in the Swarm Out tab's settings view
 * (swarm-out/SwarmOutSettings: Hives, Elastic Workers), the one place for where tasks run.
 *
 * The backend decides how many are created (exactly the difference, under the fleet
 * lock); this page only names the consequence.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { copyToClipboard } from '../../utils/clipboard';
import {
  awsAccountService, problemOf, type AwsAccountView, type Capability, type FleetStatus,
} from '../../services/awsAccount';

const MAX = 8;
const REGION_RE = /^[a-z]{2}(-[a-z]+)+-\d$/;
const DEFAULT_TYPE = 'm7g.xlarge';
const smallBtn = 'btn-md btn-secondary disabled:opacity-50';
const inputCls = 'w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]';

export function price(h: FleetStatus | null | undefined, type: string): number | null {
  const p = h?.prices?.[type];
  return typeof p === 'number' && p > 0 ? p : null;
}

/** The create button's words: the count, the monthly cost (or that it is not known:
 *  never "$0") and the account. */
function createLabel(t: (k: string, o?: Record<string, unknown>) => string, count: number,
                     unit: number | null, account: string): string {
  return unit === null
    ? t('hiveFleet.createNoPrice', { count, account })
    : t('hiveFleet.create', { count, usd: count * unit, account });
}

/** Copy the IAM policy (and the Hive role boundary) for the account's administrator. */
export function CopyTemplate() {
  const { t } = useTranslation();
  const [done, setDone] = useState<'copied' | 'failed' | null>(null);
  return (
    <button type="button" className="underline text-sm" data-testid="copy-permission-template"
      onClick={async () => {
        try {
          const tpl = await awsAccountService.permissionTemplate();
          setDone((await copyToClipboard(JSON.stringify(tpl, null, 2))) ? 'copied' : 'failed');
        } catch {
          setDone('failed');
        }
      }}>
      {done === 'copied' ? t('hiveFleet.templateCopied') : done === 'failed' ? t('hiveFleet.templateFailed') : t('hiveFleet.copyTemplate')}
    </button>
  );
}

/** "Create a default VPC in <region>": the Hive check found none; the person's click is
 *  the consent (it changes their AWS account), then the region is checked again. */
function DefaultVpcButton({ region, onView }: { region: string; onView: (v: AwsAccountView) => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <span className="block mt-1">
      <button type="button" className={smallBtn} disabled={busy} data-testid="create-default-vpc"
        onClick={async () => {
          setBusy(true); setFailed(null);
          try { onView(await awsAccountService.createDefaultVpc(region)); } catch (e) {
            setFailed(problemOf(e).message);
          } finally { setBusy(false); }
        }}>
        {busy ? t('hiveFleet.creating') : t('hiveFleet.createDefaultVpc', { region })}
      </button>
      {failed && <span className="block text-xs text-[var(--color-error)]" data-testid="create-default-vpc-failed">{failed}</span>}
    </span>
  );
}

export function CapabilityLine({ cap, checking, onView }: {
  cap: Capability | null | undefined; checking: boolean; onView?: (v: AwsAccountView) => void;
}) {
  const { t } = useTranslation();
  if (checking) return <p className="text-sm text-[var(--color-text-muted)]" data-testid="hive-cap">{t('hiveFleet.checking')}</p>;
  if (!cap) return null;
  if (cap.status === 'ok') return <p className="text-sm text-green-500" data-testid="hive-cap">{t('hiveFleet.canCreate')}</p>;
  return (
    <div className="text-sm" data-testid="hive-cap">
      <p className="text-[var(--color-warning)]">
        {cap.status === 'missing' ? t('hiveFleet.cannotCreate') : t('hiveFleet.unknown')}
      </p>
      {cap.missing && cap.missing.length > 0 && (
        <p className="text-xs text-[var(--color-text-muted)] break-words">{t('hiveFleet.missing', { actions: cap.missing.join(', ') })}</p>
      )}
      {cap.no_default_vpc && (
        <div className="text-xs text-[var(--color-text-muted)]">
          {t('hiveFleet.noDefaultVpc')}
          {onView && cap.region && <DefaultVpcButton region={cap.region} onView={onView} />}
        </div>
      )}
      {!cap.missing?.length && cap.detail && <p className="text-xs text-[var(--color-text-muted)] break-words">{cap.detail}</p>}
      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{t('hiveFleet.giveTemplate')} <CopyTemplate /></p>
    </div>
  );
}

// ── wizard step ⑤ ────────────────────────────────────────────────────────────

export function HiveSetupStep({ onDone, onBack }: { onDone: () => void; onBack?: () => void }) {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [failed, setFailed] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const [type, setType] = useState(DEFAULT_TYPE);
  const [region, setRegion] = useState('');
  // the region whose check is running (a result for another region never clears it)
  const [checkingRegion, setCheckingRegion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elastic, setElastic] = useState(true);
  const asked = useRef<Set<string>>(new Set());   // each region is checked once unasked
  const regionRef = useRef(region);
  regionRef.current = region;

  useEffect(() => {
    awsAccountService.get().then((v) => {
      setView(v);
      const h = v.hive;
      setCount(h ? Math.max(1, h.counted, h.desired ?? 1) : 1);
      setType(h?.instance_type ?? DEFAULT_TYPE);
      setRegion(h?.region ?? v.account?.region ?? 'us-east-1');
    }).catch(() => setFailed(true));
  }, []);

  // the "can it create" check runs for the region chosen here (again when it changes)
  const cap = view?.capabilities?.hive;
  useEffect(() => {
    if (!view?.account || !REGION_RE.test(region) || (cap && cap.region === region)
        || asked.current.has(region)) return undefined;
    const timer = setTimeout(() => {
      const r = region;
      asked.current.add(r);
      setCheckingRegion(r);
      awsAccountService.check(r)
        .then((v) => {
          if (regionRef.current === r) setView(v);
          else asked.current.delete(r);   // not shown: coming back to this region asks again
        })
        .catch(() => { /* the line keeps the last result */ })
        .finally(() => setCheckingRegion((cur) => (cur === r ? null : cur)));
    }, 600);
    return () => clearTimeout(timer);
  }, [region, view?.account, cap]);

  if (!view || count === null) {
    return (
      <div>
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('hiveFleet.title')}</h2>
        <p className="text-[var(--color-text-muted)]">{failed ? t('hiveFleet.loadFailed') : t('hiveFleet.loading')}</p>
        {failed && <button type="button" className="mt-6 btn-lg btn-primary" onClick={onDone}>{t('hiveFleet.continue')}</button>}
        {onBack && <button type="button" className="mt-6 ml-4 text-sm text-[var(--color-text-muted)]" onClick={onBack}>{t('awsConnect.back')}</button>}
      </div>
    );
  }

  const h = view.hive;
  const acct = view.account?.account_id ?? '';
  if (!h || h.state === 'unavailable' || !view.account) {
    return (
      <div data-testid="hive-step">
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('hiveFleet.title')}</h2>
        <p className="text-[var(--color-text-muted)] mb-6">{t('hiveFleet.connectFirst')}</p>
        <button type="button" className="btn-lg btn-primary" onClick={onDone}>{t('hiveFleet.continue')}</button>
      </div>
    );
  }

  const add = Math.max(0, count - h.counted);
  const room = Math.max(0, MAX - (h.counted_all ?? h.counted));
  const elsewhere = (h.counted_all ?? h.counted) - h.counted;   // Hives kept in other accounts
  const noRoom = add > room;
  const checking = checkingRegion === region;
  const cannot = add > 0 && cap?.status === 'missing' && cap.region === region;
  const types = Object.keys(h.prices ?? { [DEFAULT_TYPE]: 0 });
  // Elastic Workers switched off here is recorded before the wizard goes on (on is the
  // default); when that fails the step says so and stays, or the Host would turn it on
  const finish = async () => {
    if (!elastic) {
      try { await awsAccountService.setElasticStandby(false); } catch (e) {
        setError(t('hiveFleet.elasticOffFailed', { why: problemOf(e).message }));
        return;
      }
    }
    onDone();
  };
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await awsAccountService.setFleet({ desired_count: count, instance_type: type, region });
      await finish();
    } catch (e) {
      setError(problemOf(e).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="hive-step">
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">{t('hiveFleet.title')}</h2>
      <p className="text-[var(--color-text-muted)] mb-6">{t('hiveFleet.lead')}</p>
      <div className="mb-5 max-w-xl rounded-lg border border-[var(--color-border)] px-4 py-3 flex items-start gap-4" data-testid="hive-step-elastic">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm text-[var(--color-text)]">Elastic Workers <span className="ml-2 font-mono text-xs font-normal text-[var(--color-text-muted)]">AgentCore Runtime</span></div>
          <div className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t('hiveFleet.elasticChoice')}</div>
        </div>
        <label className="flex items-center gap-2 text-sm whitespace-nowrap cursor-pointer">
          <input type="checkbox" role="switch" checked={elastic} onChange={(e) => setElastic(e.target.checked)} data-testid="hive-step-elastic-switch" />
          {elastic ? t('swarmOut.settings.on') : t('swarmOut.settings.off')}
        </label>
      </div>
      <div className="mb-2 font-semibold text-sm text-[var(--color-text)]">{t('swarmOut.settings.resident')} <span className="ml-2 font-mono text-xs font-normal text-[var(--color-text-muted)]">EC2</span></div>
      {h.counted > 0 && (
        <p className="mb-4 text-sm text-[var(--color-text)]" data-testid="hive-existing">{t('hiveFleet.existing', { count: h.counted })}</p>
      )}
      <div className="grid grid-cols-[auto_1fr_1fr] gap-4 items-end max-w-xl">
        <div>
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('hiveFleet.count')}</span>
          <Stepper value={count} min={1} max={Math.max(1, Math.min(MAX, h.counted + room))} onChange={setCount} />
        </div>
        <label className="block">
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('hiveFleet.type')}</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className={inputCls} data-testid="hive-type">
            {types.map((ty) => {
              const p = price(h, ty);
              return <option key={ty} value={ty}>{p === null ? ty : `${ty} · $${p}/${t('hiveFleet.month')}`}</option>;
            })}
          </select>
        </label>
        <label className="block">
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">{t('hiveFleet.region')}</span>
          <input value={region} onChange={(e) => setRegion(e.target.value.trim())} className={inputCls} data-testid="hive-region"
            aria-invalid={!REGION_RE.test(region)} />
          {!REGION_RE.test(region) && <span className="block mt-1 text-xs text-[var(--color-warning)]">{t('awsConnect.regionShape')}</span>}
        </label>
      </div>
      <p className="mt-1 text-xs text-[var(--color-text-muted)]" data-testid="hive-limit">
        {elsewhere > 0 ? t('hiveFleet.maxElsewhere', { max: MAX, n: elsewhere }) : t('hiveFleet.max', { max: MAX })}
      </p>
      <div className="mt-4"><CapabilityLine cap={cap?.region === region ? cap : null} checking={checking} onView={setView} /></div>
      {error && <p className="mt-3 text-sm text-[var(--color-error)]" data-testid="hive-error">{error}</p>}
      <div className="mt-6 flex flex-wrap items-center gap-4">
        {onBack && (
          <button type="button" className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            onClick={onBack} data-testid="hive-back">{t('awsConnect.back')}</button>
        )}
        {cannot || noRoom ? (
          <button type="button" className="btn-lg btn-primary" onClick={() => void finish()} data-testid="hive-go">
            {noRoom ? t('hiveFleet.continueWithout') : t('hiveFleet.createLater')}
          </button>
        ) : (
          <>
            <button type="button" className="btn-lg btn-primary disabled:opacity-50" data-testid="hive-go"
              disabled={busy || !REGION_RE.test(region) || (add > 0 && checking)} onClick={() => void submit()}>
              {busy ? t('hiveFleet.creating') : add > 0 ? createLabel(t, add, price(h, type), acct) : t('hiveFleet.continue')}
            </button>
            <button type="button" className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              onClick={() => void finish()} data-testid="hive-skip">{t('hiveFleet.skip')}</button>
          </>
        )}
      </div>
      {noRoom && <p className="mt-2 text-xs text-[var(--color-warning)]" data-testid="hive-no-room">{t('hiveFleet.noRoom', { max: MAX, n: h.counted_all ?? h.counted })}</p>}
      {add > 0 && !cannot && <p className="mt-2 text-xs text-[var(--color-text-muted)]">{t('hiveFleet.notWaiting')}</p>}
    </div>
  );
}

function Stepper({ value, min, max, onChange, disabled, plusDisabled }: {
  value: number; min: number; max: number; onChange: (n: number) => void; disabled?: boolean;
  plusDisabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2" data-testid="hive-stepper" role="group" aria-label={t('hiveFleet.count')}>
      <button type="button" className={smallBtn} aria-label={t('hiveFleet.fewer')} data-testid="hive-minus"
        disabled={disabled || value <= min} onClick={() => onChange(value - 1)}>−</button>
      <span className="w-6 text-center font-mono text-[var(--color-text)]" data-testid="hive-count" aria-live="polite">{value}</span>
      <button type="button" className={smallBtn} aria-label={t('hiveFleet.more')} data-testid="hive-plus"
        disabled={disabled || plusDisabled || value >= max} onClick={() => onChange(value + 1)}>+</button>
    </div>
  );
}

// ── the settings row ─────────────────────────────────────────────────────────

