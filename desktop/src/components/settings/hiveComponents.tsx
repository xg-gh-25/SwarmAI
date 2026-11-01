/**
 * hiveComponents — the shared Hive UI building blocks, extracted from HiveTab.tsx
 * (run_b450108e, R4/R27) so BOTH surfaces consume ONE source:
 *   • HiveFleetOverlay (layout/) — the first-class SYSTEM-zone Fleet workbench
 *   • Settings → Swarm AWS Account — the Hives left in other accounts (AccountCard)
 *
 * Extraction is behavior-preserving: every export below is moved verbatim from the
 * former HiveTab.tsx (deploy progress, instance card, auth display, account card,
 * dialogs, status maps, error helper). No logic change — only the module home moved.
 *
 * Dialog note (Gate-1 D1 — VERIFIED against OverlayHost.tsx, mechanism corrected):
 * AddAccountDialog + DeployHiveDialog keep their `fixed inset-0 z-50` centered-modal
 * shape. The OverlayHost panel carries a non-`none` transform at rest (`translate-x-0
 * scale-100`, OverlayHost.tsx:182), and per CSS spec a `transform` establishes a
 * containing block for `position:fixed` descendants. So inside the Fleet overlay these
 * dialogs pin to the PANEL, not the viewport — the modal centers over the Fleet panel
 * and its `bg-black/50` scrim is scoped to the panel (it does NOT darken the leftNav).
 * That is the desired result: a self-contained modal within the workbench, no portal,
 * no z-index fight (the dialog is a content child, so its z-50 stacks above sibling
 * content within the same panel context). In the Settings tab (no transformed ancestor)
 * the SAME dialog pins to the viewport as before — behavior there is unchanged.
 */
import { useState, useEffect, useRef } from 'react';
import { hiveService, HiveAccount, HiveInstance, VerifyResult, TRANSITIONAL_STATUSES, getDeploySteps, isNewerVersion, MAX_HIVES } from '../../services/hive';
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
  { id: 'm7g.large', label: 'm7g.large — 8 GB', cost: '~$60/mo' },
  { id: 'm7g.xlarge', label: 'm7g.xlarge — 16 GB (recommended)', cost: '~$119/mo' },
  { id: 'm7g.2xlarge', label: 'm7g.2xlarge — 32 GB', cost: '~$238/mo' },
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
  return (
    <section className="bg-[var(--color-card)] rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">{title}</h3>
        {onDeploy && (
          <button
            onClick={onDeploy}
            disabled={!accounts || accounts.length === 0 || limitReached}
            title={limitReached ? `At most ${MAX_HIVES} Hives` : undefined}
            className="btn-md btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span className="material-symbols-outlined icon-sm">add</span>
            Deploy New Hive
          </button>
        )}
      </div>

      {instances.length === 0 && showEmpty ? (
        <div className="text-center py-8">
          <span className="text-4xl">{'\u{1F41D}'}</span>
          <p className="text-sm text-[var(--color-text-muted)] mt-2">
            {!accounts || accounts.length === 0
              ? 'Add an AWS account to deploy your first Hive.'
              : 'No Hives deployed yet. Click "Deploy New Hive" to get started.'}
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
    return `Retry deletes ${inst.name}'s instance and everything on it (workspace, memory, chat history), then deploys a new Hive.\n\n`
      + 'Its disks are first saved as EBS snapshots in your AWS account (named "SwarmAI-Hive-'
      + `${inst.name} before retry"); if that fails, nothing is deleted.\n\nTo keep this Hive, cancel and use Start instead.`;
  }
  return `Retry cleans up what the failed deploy of ${inst.name} left and deploys it again.`;
}

// ── Instance Card ─────────────────────────────────────────────────

export function InstanceCard({ instance: inst, onAction }: { instance: HiveInstance; onAction: () => void }) {
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
            title={url || 'URL not available yet — CloudFront still provisioning'}
            className="btn-sm btn-soft disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Open {'↗'}
          </button>
          {canUpdate && latest && (
            <button
              onClick={() => {
                if (confirm(`Update ${inst.name} from v${inst.version} to v${latest}? The Hive restarts: a task running on it stops.`)) {
                  doAction(async () => { await hiveService.updateInstance(inst.id, latest); setUpdateSent(true); });
                }
              }}
              disabled={acting}
              title={`v${latest} is the latest release`}
              className="btn-sm btn-primary disabled:opacity-50"
              data-testid="hive-update"
            >
              Update to v{latest}
            </button>
          )}
          {inst.status === 'running' && (
            <button
              onClick={() => doAction(() => hiveService.stopInstance(inst.id))}
              disabled={acting}
              className="btn-sm btn-warning disabled:opacity-50"
            >
              Stop
            </button>
          )}
          {(inst.status === 'stopped' || (inst.status === 'error' && !!inst.ec2InstanceId)) && (
            <button
              onClick={() => doAction(() => hiveService.startInstance(inst.id))}
              disabled={acting}
              className="btn-sm btn-success disabled:opacity-50"
            >
              Start
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
              Retry
            </button>
          )}
          {!isTransitional && (
            <button
              onClick={() => {
                if (confirm(`Delete ${inst.name}? Its instance and everything on it (workspace, memory, chat history) are deleted. This cannot be undone.`)) {
                  doAction(() => hiveService.deleteInstance(inst.id));
                }
              }}
              disabled={acting}
              className="btn-sm btn-danger disabled:opacity-50"
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {/* Owner name for shared Hives */}
      {inst.ownerName && (
        <div className="text-xs text-[var(--color-text-muted)] mb-2">
          Owner: {inst.ownerName}
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
          Updating: installing the new version and checking its health (a few minutes).
        </p>
      ) : isTransitional && <DeployProgress instance={inst} />}

      {/* Auth display + share for running instances */}
      {inst.status === 'running' && inst.authUser && <AuthDisplay instance={inst} />}

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
  const steps = getDeploySteps(instance);
  const currentIdx = steps.findIndex(s => !s.done);

  return (
    <div className="mt-2 space-y-1">
      {steps.map((step, i) => (
        <div key={step.label} className="flex items-center gap-2 text-xs">
          {step.done ? (
            <span className="text-green-400 w-4 text-center">{'✓'}</span>
          ) : i === currentIdx ? (
            <span className="inline-block w-3 h-3 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin ml-0.5" />
          ) : (
            <span className="w-4 text-center text-[var(--color-text-muted)]">{'○'}</span>
          )}
          <span className={step.done ? 'text-[var(--color-text-muted)]' : i === currentIdx ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]/50'}>
            {step.label}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Auth Display + Share ──────────────────────────────────────────

export function AuthDisplay({ instance: inst }: { instance: HiveInstance }) {
  const [showPass, setShowPass] = useState(false);
  const [password, setPassword] = useState<string | null>(inst.authPassword);
  const [loadingPass, setLoadingPass] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, []);

  const url = inst.cloudfrontDomain
    ? `https://${inst.cloudfrontDomain}`
    : inst.ec2PublicIp ? `http://${inst.ec2PublicIp}` : '';

  // Fetch password from credentials endpoint when Show is clicked
  const togglePass = async () => {
    if (showPass) {
      // Hiding
      setShowPass(false);
      return;
    }
    // Showing — fetch real password if not already loaded
    setFetchError(null);
    if (!password) {
      setLoadingPass(true);
      try {
        const creds = await hiveService.getCredentials(inst.id);
        setPassword(creds.authPassword);
      } catch (e) {
        console.error('Failed to fetch credentials:', e);
        setFetchError('Failed to load password');
        setLoadingPass(false);
        return;
      }
      setLoadingPass(false);
    }
    setShowPass(true);
    // Auto-hide after 30s
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setShowPass(false), 30000);
  };

  const doCopy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    }
  };

  const handleCopyPassword = async () => {
    // Fetch password if not loaded yet
    let pass = password;
    if (!pass) {
      try {
        const creds = await hiveService.getCredentials(inst.id);
        pass = creds.authPassword;
        setPassword(pass);
      } catch { return; }
    }
    if (pass) await doCopy(pass, 'password');
  };

  // Share generates copyable text (fetches password if needed)
  const handleShare = async () => {
    let pass = password;
    if (!pass) {
      try {
        const creds = await hiveService.getCredentials(inst.id);
        pass = creds.authPassword;
        setPassword(pass);
      } catch { return; }
    }
    const shareText = [
      `Your SwarmAI Hive is ready:`,
      `URL: ${url}`,
      `User: ${inst.authUser}`,
      `Password: ${pass}`,
      ``,
      `Open the URL and sign in. It works exactly like the desktop app.`,
    ].join('\n');
    await doCopy(shareText, 'share');
  };

  // Compute display value — separated from JSX for clarity
  const passDisplay = loadingPass ? '...' : (showPass && password) ? password : '••••••••';

  return (
    <div className="mt-2 p-2 bg-[var(--color-card)] rounded text-xs space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[var(--color-text-muted)] w-12">Auth:</span>
        <code className="text-[var(--color-text)]">{inst.authUser}</code>
        <span className="text-[var(--color-text-muted)]">/</span>
        <code className="text-[var(--color-text)] select-all">{passDisplay}</code>
        <button onClick={togglePass} disabled={loadingPass}
          className="btn-sm btn-secondary disabled:opacity-50">
          {loadingPass ? '...' : showPass ? 'Hide' : 'Show'}
        </button>
        <button onClick={handleCopyPassword}
          className="btn-sm btn-secondary">
          {copied === 'password' ? 'Copied!' : 'Copy'}
        </button>
      </div>
      {fetchError && (
        <div className="text-red-400 text-[11px]">{fetchError}</div>
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={handleShare}
          className="btn-md btn-soft"
        >
          <span className="material-symbols-outlined icon-sm">share</span>
          {copied === 'share' ? 'Copied!' : 'Share'}
        </button>
        <span className="text-[var(--color-text-muted)]">Copy URL + credentials for the Hive user</span>
      </div>
    </div>
  );
}

// ── Account Card ───────────────────────────────────────────────────

export function AccountCard({ account: acc, onDelete }: { account: HiveAccount; onDelete: () => void }) {
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
    if (!confirm('Delete this account and all its Hives? Each Hive\'s instance and everything on it (workspace, memory, chat history) are deleted. This cannot be undone.')) return;
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
                ? <>New Hives are created here {'·'} change it in Settings {'>'} SwarmAI account</>
                : acc.role === 'disconnected'
                  ? <>The SwarmAI account is not connected {'·'} connect it in Settings {'>'} SwarmAI account to manage these Hives</>
                : acc.role === 'other'
                  ? <>Another AWS account: its {acc.hiveCount} Hive{acc.hiveCount === 1 ? '' : 's'} can only be stopped or deleted. Verify if it is the SwarmAI account.</>
                  : <>{acc.authMethod} {'·'} {acc.defaultRegion}</>}
              {acc.verifiedAt && <span className="text-green-400 ml-2">{'✓'} verified</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleVerify}
            disabled={verifying}
            className="btn-sm btn-secondary disabled:opacity-50"
          >
            {verifying ? '...' : 'Verify'}
          </button>
          {acc.role !== 'swarmai' && acc.role !== 'disconnected' && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              aria-label="Delete this account and its Hives"
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
              ? '✓ This is the SwarmAI account: its Hives were moved there'
              : '✓ All checks passed'
            : `✗ ${verifyResult.error || 'Failed'}`}
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
    if (!accountId.match(/^\d{12}$/)) { setError('Account ID must be 12 digits'); return; }
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

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={saving ? undefined : onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hive-add-account-title" tabIndex={-1}
        className="bg-[var(--color-card)] rounded-xl p-6 w-[440px] max-h-[80vh] overflow-y-auto outline-none" onClick={e => e.stopPropagation()}>
        <h3 id="hive-add-account-title" className="text-lg font-semibold text-[var(--color-text)] mb-4">Add AWS Account</h3>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account ID</label>
            <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="Enter 12-digit account ID"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Label (optional)</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. personal, team-shared"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          <div className="grid grid-cols-2 gap-2">
            {(['access_keys', 'sso'] as const).map((m) => (
              <button key={m} onClick={() => setAuthMethod(m)}
                className={`p-2 rounded-lg text-left text-xs ${authMethod === m ? 'bg-[var(--color-primary)]/20 border-2 border-[var(--color-primary)]' : 'bg-[var(--color-bg)] border border-[var(--color-border)]'}`}>
                <div className="font-medium text-[var(--color-text)]">{m === 'access_keys' ? 'Access Keys' : 'SSO Profile'}</div>
              </button>
            ))}
          </div>

          {authMethod === 'access_keys' && (
            <>
              <input value={accessKeyId} onChange={(e) => setAccessKeyId(e.target.value)} placeholder="Access Key ID (AKIA...)"
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
              <input type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} placeholder="Secret Access Key"
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
            </>
          )}
          {authMethod === 'sso' && (
            <input value={ssoProfile} onChange={(e) => setSsoProfile(e.target.value)} placeholder="SSO profile name"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          )}

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Region</label>
            <select value={region} onChange={(e) => setRegion(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]">
              <option value="us-east-1">US East (Virginia)</option>
              <option value="us-west-2">US West (Oregon)</option>
              <option value="eu-west-1">EU (Ireland)</option>
              <option value="ap-northeast-1">Asia Pacific (Tokyo)</option>
            </select>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={saving} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">Cancel</button>
          <button onClick={handleSave} disabled={saving || !accountId}
            className="btn-lg btn-primary disabled:opacity-50">
            {saving ? 'Saving...' : 'Add Account'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Deploy Hive Dialog ─────────────────────────────────────────────

export function DeployHiveDialog({ accounts, onClose, onDeployed }: { accounts: HiveAccount[]; onClose: () => void; onDeployed: () => void }) {
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
      setError('Name: 2-63 chars, lowercase letters/numbers/hyphens, must start and end with a letter or number');
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

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={deploying ? undefined : onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hive-deploy-title" tabIndex={-1}
        className="bg-[var(--color-card)] rounded-xl p-6 w-[440px] outline-none" onClick={e => e.stopPropagation()}>
        <h3 id="hive-deploy-title" className="text-lg font-semibold text-[var(--color-text)] mb-4">Deploy New Hive</h3>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Hive Name</label>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="e.g. xg-hive, dev-01"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          {/* Owner name field */}
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Owner (optional — leave blank if this is for you)</label>
            <input value={ownerName} onChange={(e) => setOwnerName(e.target.value)} placeholder="e.g. teammate name"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account</label>
            {accounts.length === 1 && accounts[0].role === 'swarmai' ? (
              <div className="px-3 py-2 text-sm text-[var(--color-text)]" data-testid="hive-deploy-account">
                <code>{accounts[0].accountId}</code>
                <span className="text-[var(--color-text-muted)] ml-2">(SwarmAI account)</span>
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
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Instance Size</label>
            <div className="space-y-1.5">
              {INSTANCE_SIZES.map((s) => (
                <button key={s.id} onClick={() => setInstanceType(s.id)}
                  className={`w-full p-2.5 rounded-lg text-left text-xs flex justify-between ${instanceType === s.id ? 'bg-[var(--color-primary)]/20 border-2 border-[var(--color-primary)]' : 'bg-[var(--color-bg)] border border-[var(--color-border)]'}`}>
                  <span className="text-[var(--color-text)]">{s.label}</span>
                  <span className="text-[var(--color-text-muted)]">{s.cost}</span>
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={deploying} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">Cancel</button>
          <button onClick={handleDeploy} disabled={deploying || !name || !accountRef}
            className="btn-lg btn-primary disabled:opacity-50">
            {deploying ? 'Deploying...' : 'Deploy'}
          </button>
        </div>
      </div>
    </div>
  );
}
