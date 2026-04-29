/**
 * Hive cloud instance management tab.
 *
 * Live management panel: deploy progress, status polling,
 * auth credentials display, share, split My/Shared Hives.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  hiveService,
  HiveAccount,
  HiveInstance,
  VerifyResult,
  TRANSITIONAL_STATUSES,
  getDeploySteps,
} from '../../services/hive';
import { openExternal } from '../../utils/openExternal';
import { copyToClipboard } from '../../utils/clipboard';
import { isApiError } from '../../services/api';

/** Extract the most useful message from an API error. */
function friendlyError(e: unknown): string {
  if (isApiError(e)) {
    // Prefer .detail (backend's actual message), fall back to .message
    return e.detail || e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

const STATUS_COLORS: Record<string, string> = {
  running: 'text-green-400',
  stopped: 'text-yellow-400',
  pending: 'text-blue-400',
  provisioning: 'text-blue-400',
  installing: 'text-blue-400',
  error: 'text-red-400',
  deleting: 'text-red-400',
};

const STATUS_ICONS: Record<string, string> = {
  running: '\u{1F7E2}',
  stopped: '\u{1F534}',
  pending: '\u{1F535}',
  provisioning: '\u{1F535}',
  installing: '\u{1F535}',
  error: '❌',
  deleting: '\u{1F534}',
};

const INSTANCE_SIZES = [
  { id: 'm7g.large', label: 'm7g.large — 8 GB', cost: '~$60/mo' },
  { id: 'm7g.xlarge', label: 'm7g.xlarge — 16 GB (recommended)', cost: '~$119/mo' },
  { id: 'm7g.2xlarge', label: 'm7g.2xlarge — 32 GB', cost: '~$238/mo' },
];

export default function HiveTab() {
  const [accounts, setAccounts] = useState<HiveAccount[]>([]);
  const [instances, setInstances] = useState<HiveInstance[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddAccount, setShowAddAccount] = useState(false);
  const [showDeployHive, setShowDeployHive] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const [accs, insts] = await Promise.all([
        hiveService.listAccounts(),
        hiveService.listInstances(),
      ]);
      setAccounts(accs);
      setInstances(insts);
    } catch (e) {
      console.error('Failed to load Hive data:', e);
    } finally {
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // AC2: Poll every 5s when any instance is in transitional state
  useEffect(() => {
    const hasTransitional = instances.some(i => TRANSITIONAL_STATUSES.includes(i.status));
    if (hasTransitional && !pollRef.current) {
      pollRef.current = setInterval(refresh, 5000);
    } else if (!hasTransitional && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [instances, refresh]);

  // AC5: Split My Hives vs Shared Hives
  const myHives = instances.filter(i => i.hiveType === 'my');
  const sharedHives = instances.filter(i => i.hiveType !== 'my');

  if (loading) {
    return <div className="text-[var(--color-text-muted)] text-sm p-6">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text)]">
            {'\u{1F41D}'} Hive — Your AI in the Cloud
          </h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Deploy SwarmAI to your AWS account. Each Hive has its own memory, skills, and workspace.
          </p>
        </div>
      </div>

      {/* My Hives */}
      {myHives.length > 0 && (
        <HiveSection title="My Hives" instances={myHives} onAction={refresh} />
      )}

      {/* Shared Hives (or all instances if none are typed) */}
      <HiveSection
        title={myHives.length > 0 ? 'Shared Hives' : 'Hive Instances'}
        instances={myHives.length > 0 ? sharedHives : instances}
        onAction={refresh}
        accounts={accounts}
        onDeploy={() => setShowDeployHive(true)}
        showEmpty
      />

      {/* AWS Accounts */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">AWS Accounts</h3>
          <button
            onClick={() => setShowAddAccount(true)}
            className="px-3 py-1.5 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">add</span>
            Add Account
          </button>
        </div>

        {accounts.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)] text-center py-4">
            No AWS accounts configured. Add one to start deploying Hives.
          </p>
        ) : (
          <div className="space-y-2">
            {accounts.map((acc) => (
              <AccountCard key={acc.id} account={acc} onDelete={refresh} />
            ))}
          </div>
        )}
      </section>

      {showAddAccount && (
        <AddAccountDialog
          onClose={() => setShowAddAccount(false)}
          onSaved={() => { setShowAddAccount(false); refresh(); }}
        />
      )}

      {showDeployHive && (
        <DeployHiveDialog
          accounts={accounts}
          onClose={() => setShowDeployHive(false)}
          onDeployed={() => { setShowDeployHive(false); refresh(); }}
        />
      )}
    </div>
  );
}


// ── Hive Section ──────────────────────────────────────────────────

function HiveSection({
  title, instances, onAction, accounts, onDeploy, showEmpty,
}: {
  title: string;
  instances: HiveInstance[];
  onAction: () => void;
  accounts?: HiveAccount[];
  onDeploy?: () => void;
  showEmpty?: boolean;
}) {
  return (
    <section className="bg-[var(--color-card)] rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">{title}</h3>
        {onDeploy && (
          <button
            onClick={onDeploy}
            disabled={!accounts || accounts.length === 0}
            className="px-3 py-1.5 text-xs bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">add</span>
            Deploy New Hive
          </button>
        )}
      </div>

      {instances.length === 0 && showEmpty ? (
        <div className="text-center py-8">
          <span className="text-4xl">{'\u{1F41D}'}</span>
          <p className="text-sm text-[var(--color-text-muted)] mt-2">
            {!accounts || accounts.length === 0
              ? 'Add an AWS account below to deploy your first Hive.'
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


// ── Instance Card (enhanced) ──────────────────────────────────────

function InstanceCard({ instance: inst, onAction }: { instance: HiveInstance; onAction: () => void }) {
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
          {url && (
            <button
              onClick={() => openExternal(url)}
              className="px-2 py-1 text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)] rounded hover:bg-[var(--color-primary)]/30"
            >
              Open {'↗'}
            </button>
          )}
          {inst.status === 'running' && (
            <button
              onClick={() => doAction(() => hiveService.stopInstance(inst.id))}
              disabled={acting}
              className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 rounded hover:bg-yellow-500/30 disabled:opacity-50"
            >
              Stop
            </button>
          )}
          {inst.status === 'stopped' && (
            <button
              onClick={() => doAction(() => hiveService.startInstance(inst.id))}
              disabled={acting}
              className="px-2 py-1 text-xs bg-green-500/20 text-green-400 rounded hover:bg-green-500/30 disabled:opacity-50"
            >
              Start
            </button>
          )}
          {inst.status === 'error' && (
            <button
              onClick={() => doAction(() => hiveService.retryInstance(inst.id))}
              disabled={acting}
              className="px-2 py-1 text-xs bg-blue-500/20 text-blue-400 rounded hover:bg-blue-500/30 disabled:opacity-50"
            >
              Retry
            </button>
          )}
          {!isTransitional && (
            <button
              onClick={() => { if (confirm(`Delete ${inst.name}?`)) doAction(() => hiveService.deleteInstance(inst.id)); }}
              disabled={acting}
              className="px-2 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 disabled:opacity-50"
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {/* AC6: Owner name for shared Hives */}
      {inst.ownerName && (
        <div className="text-xs text-[var(--color-text-muted)] mb-2">
          Owner: {inst.ownerName}
        </div>
      )}

      {/* Meta row */}
      <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)] mb-2">
        {url && <code className="bg-[var(--color-card)] px-1.5 py-0.5 rounded select-all text-[10px]">{url}</code>}
        <span>{inst.instanceType}</span>
        <span>{inst.region}</span>
        {inst.version && <span>v{inst.version}</span>}
      </div>

      {/* AC1: Deploy progress for transitional states */}
      {isTransitional && <DeployProgress instance={inst} />}

      {/* AC3+4: Auth display + share for running instances */}
      {inst.status === 'running' && inst.authUser && <AuthDisplay instance={inst} />}

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

function DeployProgress({ instance }: { instance: HiveInstance }) {
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

function AuthDisplay({ instance: inst }: { instance: HiveInstance }) {
  const [showPass, setShowPass] = useState(false);
  const [password, setPassword] = useState<string | null>(inst.authPassword);
  const [loadingPass, setLoadingPass] = useState(false);
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
    if (!showPass) {
      // Showing — fetch real password if not already loaded
      if (!password) {
        setLoadingPass(true);
        try {
          const creds = await hiveService.getCredentials(inst.id);
          setPassword(creds.authPassword);
        } catch (e) {
          console.error('Failed to fetch credentials:', e);
          setLoadingPass(false);
          return;
        }
        setLoadingPass(false);
      }
      setShowPass(true);
      // Auto-hide after 30s
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => setShowPass(false), 30000);
    } else {
      setShowPass(false);
    }
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

  // AC4: Share generates copyable text (fetches password if needed)
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

  return (
    <div className="mt-2 p-2 bg-[var(--color-card)] rounded text-xs space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[var(--color-text-muted)] w-12">Auth:</span>
        <code className="text-[var(--color-text)]">{inst.authUser}</code>
        <span className="text-[var(--color-text-muted)]">/</span>
        <code className="text-[var(--color-text)]">
          {loadingPass ? '...' : showPass && password ? password : '••••••••'}
        </code>
        <button onClick={togglePass} disabled={loadingPass}
          className="px-1.5 py-0.5 text-[10px] bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] disabled:opacity-50">
          {loadingPass ? '...' : showPass ? 'Hide' : 'Show'}
        </button>
        <button onClick={handleCopyPassword}
          className="px-1.5 py-0.5 text-[10px] bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)]">
          {copied === 'password' ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={handleShare}
          className="px-2 py-1 bg-[var(--color-primary)]/20 text-[var(--color-primary)] rounded hover:bg-[var(--color-primary)]/30 flex items-center gap-1"
        >
          <span className="material-symbols-outlined text-sm">share</span>
          {copied === 'share' ? 'Copied!' : 'Share'}
        </button>
        <span className="text-[var(--color-text-muted)]">Copy URL + credentials for the Hive user</span>
      </div>
    </div>
  );
}


// ── Account Card ───────────────────────────────────────────────────

function AccountCard({ account: acc, onDelete }: { account: HiveAccount; onDelete: () => void }) {
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
    if (!confirm('Delete account + all its Hives?')) return;
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
            <div className="text-xs text-[var(--color-text-muted)]">
              {acc.authMethod} {'·'} {acc.defaultRegion}
              {acc.verifiedAt && <span className="text-green-400 ml-2">{'✓'} verified</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleVerify}
            disabled={verifying}
            className="px-2 py-1 text-xs bg-[var(--color-card)] text-[var(--color-text-muted)] rounded hover:bg-[var(--color-primary)] hover:text-white disabled:opacity-50"
          >
            {verifying ? '...' : 'Verify'}
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="px-2 py-1 text-xs text-red-400 hover:bg-red-500/20 rounded disabled:opacity-50"
          >
            {deleting ? '...' : '✕'}
          </button>
        </div>
      </div>
      {verifyResult && (
        <div className={`mt-2 text-xs ${verifyResult.success ? 'text-green-400' : 'text-red-400'}`}>
          {verifyResult.success ? '✓ All checks passed' : `✗ ${verifyResult.error || 'Failed'}`}
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

function AddAccountDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [accountId, setAccountId] = useState('');
  const [label, setLabel] = useState('');
  const [region, setRegion] = useState('us-east-1');
  const [authMethod, setAuthMethod] = useState<'access_keys' | 'sso'>('access_keys');
  const [accessKeyId, setAccessKeyId] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [ssoProfile, setSsoProfile] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !saving) onClose(); };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose, saving]);

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
      <div className="bg-[var(--color-card)] rounded-xl p-6 w-[440px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-[var(--color-text)] mb-4">Add AWS Account</h3>

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
            className="px-4 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
            {saving ? 'Saving...' : 'Add Account'}
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Deploy Hive Dialog (enhanced: owner_name) ─────────────────────

function DeployHiveDialog({ accounts, onClose, onDeployed }: { accounts: HiveAccount[]; onClose: () => void; onDeployed: () => void }) {
  const [name, setName] = useState('');
  const [ownerName, setOwnerName] = useState('');
  const [accountRef, setAccountRef] = useState(accounts[0]?.id ?? '');
  const [instanceType, setInstanceType] = useState('m7g.xlarge');
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !deploying) onClose(); };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose, deploying]);

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
      <div className="bg-[var(--color-card)] rounded-xl p-6 w-[440px]" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-[var(--color-text)] mb-4">Deploy New Hive</h3>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Hive Name</label>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="e.g. xg-hive, dev-01"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          {/* AC7: Owner name field */}
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Owner (optional — leave blank if this is for you)</label>
            <input value={ownerName} onChange={(e) => setOwnerName(e.target.value)} placeholder="e.g. Bo Wang, Titus Tian"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]" />
          </div>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account</label>
            <select value={accountRef} onChange={(e) => setAccountRef(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]">
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.accountId} {a.label ? `(${a.label})` : ''}</option>
              ))}
            </select>
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
            className="px-4 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
            {deploying ? 'Deploying...' : 'Deploy'}
          </button>
        </div>
      </div>
    </div>
  );
}
