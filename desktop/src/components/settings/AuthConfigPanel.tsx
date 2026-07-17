/**
 * Shared auth configuration panel.
 *
 * Used by both OnboardingPage (mode="onboarding") and Settings AI & Models tab (mode="settings").
 * Handles auth method selection, credential status, and verify connection.
 */
import { useState, useEffect } from 'react';
import { systemService, VerifyAuthResponse, AuthHintResponse } from '../../services/system';
import { settingsService } from '../../services/settings';
import { Dropdown } from '../common';

type AuthMethod = 'sso' | 'ada' | 'apikey';

interface AuthConfigPanelProps {
  mode: 'onboarding' | 'settings';
  onVerifySuccess?: () => void;
  onVerifyFail?: () => void;
}

const AWS_REGION_OPTIONS = [
  { id: 'us-east-1', name: 'US East (N. Virginia)', description: 'us-east-1' },
  { id: 'us-west-2', name: 'US West (Oregon)', description: 'us-west-2' },
  { id: 'eu-west-1', name: 'EU (Ireland)', description: 'eu-west-1' },
  { id: 'eu-central-1', name: 'EU (Frankfurt)', description: 'eu-central-1' },
  { id: 'ap-northeast-1', name: 'Asia Pacific (Tokyo)', description: 'ap-northeast-1' },
  { id: 'ap-southeast-1', name: 'Asia Pacific (Singapore)', description: 'ap-southeast-1' },
];

export default function AuthConfigPanel({ mode, onVerifySuccess, onVerifyFail }: AuthConfigPanelProps) {
  const [method, setMethod] = useState<AuthMethod>('sso');
  const [region, setRegion] = useState('us-east-1');
  const [accountId, setAccountId] = useState('');
  const [adaAccount, setAdaAccount] = useState('');
  const [adaRole, setAdaRole] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [verifyState, setVerifyState] = useState<'idle' | 'verifying' | 'success' | 'error'>('idle');
  const [verifyResult, setVerifyResult] = useState<VerifyAuthResponse | null>(null);
  const [authHint, setAuthHint] = useState<AuthHintResponse | null>(null);
  // Deployment context drives which method cards show. Detected by the backend
  // (internal iff ~/.ada|~/.midway), overridable by the user via a one-click
  // toggle (an internal employee on a fresh machine, or vice versa).
  const [context, setContext] = useState<'internal' | 'external'>('external');
  const [contextOverridden, setContextOverridden] = useState(false);

  // Auto-detect best auth method and load real credential details
  useEffect(() => {
    systemService.getAuthHint()
      .then((hint) => {
        setAuthHint(hint);
        // Adopt detected context unless the user already toggled it.
        if (!contextOverridden && hint.deploymentContext) {
          setContext(hint.deploymentContext);
        }
        // Map backend suggestion to UI method (iam_role → sso for Hive)
        const methodMap: Record<string, AuthMethod> = {
          'ada': 'ada', 'sso': 'sso', 'apikey': 'apikey', 'iam_role': 'sso',
        };
        setMethod(methodMap[hint.suggestedMethod] || 'sso');
        // Pre-fill from probed credentials — IAM details take priority (Hive),
        // then Ada details (Amazon internal), so the user sees real values on load
        if (hint.iamDetails) {
          if (hint.iamDetails.accountId) setAccountId(hint.iamDetails.accountId);
          if (hint.iamDetails.region) setRegion(hint.iamDetails.region);
        } else if (hint.adaDetails) {
          if (hint.adaDetails.accountId) {
            setAccountId(hint.adaDetails.accountId);
            setAdaAccount(hint.adaDetails.accountId);
          }
          if (hint.adaDetails.roleName) setAdaRole(hint.adaDetails.roleName);
        }
      })
      .catch(() => { /* default sso is fine */ });
  }, []);

  // Load current config from settings (region)
  useEffect(() => {
    settingsService.getAPIConfiguration()
      .then((config) => {
        if (config.awsRegion) setRegion(config.awsRegion);
      })
      .catch(() => {});
  }, []);

  const handleVerify = async () => {
    setVerifyState('verifying');
    setVerifyResult(null);

    try {
      // Verify FIRST with the attempted (not-yet-persisted) config, and persist
      // ONLY after a successful verify. A failed verify must leave config
      // untouched (otherwise a wrong region/method silently persists).
      const isBedrock = method !== 'apikey';
      const configUpdate: Record<string, unknown> = {
        use_bedrock: isBedrock,
        aws_region: region,
      };
      if (method === 'ada') {
        configUpdate.ada_account = adaAccount;
        configUpdate.ada_role = adaRole;
      }
      // For Anthropic-direct, pass the entered key so verify can validate it
      // even before it's persisted (backend reads override.anthropic_api_key).
      if (method === 'apikey' && apiKey.trim()) {
        configUpdate.anthropic_api_key = apiKey.trim();
      }

      const result = await systemService.verifyAuth(configUpdate);
      setVerifyResult(result);
      setVerifyState(result.success ? 'success' : 'error');

      if (result.success) {
        // Persist the API key via the dedicated secret endpoint (NOT settings —
        // secrets are stripped there). Then persist non-secret config + method.
        if (method === 'apikey' && apiKey.trim()) {
          await systemService.persistApiKey(apiKey.trim());
          setApiKey('');  // don't keep the secret in component memory after persist
        }
        await settingsService.updateAPIConfiguration(configUpdate);
        // Persist the chosen method + context so error remediation is method-aware.
        await systemService.setAuthMethod(method, context);
        if (onVerifySuccess) onVerifySuccess();
      } else if (onVerifyFail) {
        onVerifyFail();
      }
    } catch (e) {
      setVerifyResult({
        success: false,
        error: String(e),
        errorType: 'unknown',
        fixHint: 'Check your network connection and try again.',
      });
      setVerifyState('error');
      if (onVerifyFail) onVerifyFail();
    }
  };

  // Method cards are filtered by deployment context:
  //   internal → [ADA, SSO]        (Amazon employees: ADA or corporate SSO)
  //   external → [SSO, Anthropic]  (others: personal-AWS SSO, or Anthropic key)
  // SSO is shared by both (same `aws sso login` → Bedrock, identity-agnostic).
  const methods: { id: AuthMethod; label: string; desc: string }[] =
    context === 'internal'
      ? [
          { id: 'ada', label: 'Ada', desc: 'Amazon Internal' },
          { id: 'sso', label: 'AWS SSO', desc: 'Identity Center' },
        ]
      : [
          { id: 'sso', label: 'AWS SSO', desc: 'Identity Center' },
          { id: 'apikey', label: 'API Key', desc: 'Anthropic Direct' },
        ];

  // If the current method isn't valid for this context, snap to the first card.
  useEffect(() => {
    if (!methods.some(m => m.id === method)) {
      setMethod(methods[0].id);
      setVerifyState('idle');
      setVerifyResult(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context]);

  const toggleContext = () => {
    setContextOverridden(true);
    setContext(c => (c === 'internal' ? 'external' : 'internal'));
    setVerifyState('idle');
    setVerifyResult(null);
  };

  // Hive mode: single fixed auth method, no choices
  const isHiveIam = authHint?.runMode === 'hive' && authHint?.suggestedMethod === 'iam_role';
  const iam = authHint?.iamDetails;

  // Shared verify button + result — used by both Hive and desktop layouts
  const renderVerifySection = () => (
    <>
      <button
        onClick={handleVerify}
        disabled={verifyState === 'verifying'}
        className="w-full px-4 py-2.5 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50 flex items-center justify-center gap-2 font-medium"
      >
        {verifyState === 'verifying' ? (
          <>
            <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
            Verifying...
          </>
        ) : (
          <>
            <span className="material-symbols-outlined text-sm">play_arrow</span>
            Verify Connection
          </>
        )}
      </button>

      {verifyState === 'success' && verifyResult && (
        <div className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-green-400">check_circle</span>
          <span className="text-green-400 text-sm">
            {verifyResult.model} responded in {verifyResult.latencyMs}ms
          </span>
        </div>
      )}

      {verifyState === 'error' && verifyResult && (
        <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
          <div className="flex items-center gap-2 mb-1">
            <span className="material-symbols-outlined text-red-400 text-sm">error</span>
            <span className="text-red-400 text-sm font-medium">
              {verifyResult.errorType === 'expired_credentials' ? 'Credentials Expired' :
               verifyResult.errorType === 'missing_key' ? 'API Key Not Found' :
               verifyResult.errorType === 'invalid_key' ? 'Invalid API Key' :
               verifyResult.errorType === 'access_denied' ? 'Access Denied' :
               'Connection Failed'}
            </span>
          </div>
          {verifyResult.fixHint && (
            <p className="text-xs text-[var(--color-text-muted)]">{verifyResult.fixHint}</p>
          )}
        </div>
      )}

      {mode === 'onboarding' && verifyState !== 'success' && (
        <p className="text-xs text-[var(--color-text-muted)] text-center">
          Must verify before proceeding.
        </p>
      )}
    </>
  );

  // ── Hive layout: read-only summary + verify ──
  if (isHiveIam) {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-[var(--color-card)] rounded-lg space-y-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="w-2 h-2 bg-green-400 rounded-full" />
            <span className="text-sm font-medium text-green-400">EC2 IAM Instance Role</span>
          </div>

          <div className="space-y-2 text-xs">
            {iam?.accountId && (
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">Account</span>
                <code className="text-[var(--color-text)]">{iam.accountId}</code>
              </div>
            )}
            {iam?.region && (
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">Region</span>
                <code className="text-[var(--color-text)]">{iam.region}</code>
              </div>
            )}
            {iam?.roleName && (
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">Role</span>
                <code className="text-[var(--color-text)]">{iam.roleName}</code>
              </div>
            )}
            {iam?.instanceId && (
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">Instance</span>
                <code className="text-[var(--color-text)]">{iam.instanceId}</code>
              </div>
            )}
          </div>

          <p className="text-xs text-[var(--color-text-muted)] pt-1">
            Credentials are managed by the EC2 instance role — no configuration needed.
          </p>
        </div>

        {/* Verify + result (shared with desktop layout below) */}
        {renderVerifySection()}
      </div>
    );
  }

  // ── Desktop layout: method selector + config fields ──
  return (
    <div className="space-y-4">
      {/* Section title */}
      {mode === 'onboarding' && (
        <p className="text-xs text-[var(--color-text-muted)]">
          SwarmAI uses your AWS account for Claude AI, cloud deployment, and other services.
        </p>
      )}

      {/* Auth method cards */}
      <div className={`grid gap-3 ${methods.length <= 3 ? 'grid-cols-3' : 'grid-cols-4'}`}>
        {methods.map((m) => (
          <button
            key={m.id}
            onClick={() => { setMethod(m.id); setVerifyState('idle'); setVerifyResult(null); }}
            className={`p-3 rounded-lg text-left transition-all ${
              method === m.id
                ? 'bg-[var(--color-primary)]/20 border-2 border-[var(--color-primary)]'
                : 'bg-[var(--color-card)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            <div className="text-sm font-medium text-[var(--color-text)]">{m.label}</div>
            <div className="text-xs text-[var(--color-text-muted)]">{m.desc}</div>
          </button>
        ))}
      </div>

      {/* Config fields based on method */}
      {method !== 'apikey' && (
        <div className="space-y-3">
          {/* AWS Account ID.
              - ADA: editable — the value builds the `ada credentials update` command below.
              - SSO: READ-ONLY probed value. boto3/SSO reads the account from the
                active profile, not from this field; an editable input here was
                silently discarded (its value was never persisted). Show it as
                context, not a dead input. Hidden entirely if nothing was probed. */}
          {method === 'ada' ? (
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account ID</label>
              <input
                type="text"
                value={accountId}
                onChange={(e) => { setAccountId(e.target.value); setAdaAccount(e.target.value); }}
                placeholder="Enter your 12-digit AWS account ID"
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
          ) : accountId ? (
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account ID</label>
              <div className="w-full px-3 py-2 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text-muted)] flex items-center justify-between">
                <code className="text-[var(--color-text)]">{accountId}</code>
                <span className="text-[10px] uppercase tracking-wide opacity-60">from AWS profile</span>
              </div>
            </div>
          ) : null}

          <Dropdown
            label="AWS Region"
            options={AWS_REGION_OPTIONS}
            selectedId={region}
            onChange={setRegion}
            placeholder="Select region..."
          />

          {/* ADA-specific fields */}
          {method === 'ada' && (
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">ADA Role</label>
              <input
                type="text"
                value={adaRole}
                onChange={(e) => setAdaRole(e.target.value)}
                placeholder="e.g. Admin"
                className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
          )}

          {/* Credential status / setup hint */}
          <div className="p-3 bg-[var(--color-card)] rounded-lg text-xs">
            {method === 'ada' ? (() => {
              const displayAccount = adaAccount || authHint?.adaDetails?.accountId || '<ACCOUNT>';
              const displayRole = adaRole || authHint?.adaDetails?.roleName || '<ROLE>';
              const hasRealValues = !!(adaAccount || authHint?.adaDetails?.accountId);

              return authHint?.adaDetails?.configured ? (
                <>
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                    <span className="text-green-400 font-medium">ADA credentials active</span>
                  </div>
                  <div className="space-y-1 text-[var(--color-text-muted)]">
                    {authHint.adaDetails?.accountId && (
                      <div className="flex justify-between">
                        <span>Account</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.accountId}</code>
                      </div>
                    )}
                    {authHint.adaDetails?.roleName && (
                      <div className="flex justify-between">
                        <span>Role</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.roleName}</code>
                      </div>
                    )}
                    {authHint.adaDetails?.keyPrefix && (
                      <div className="flex justify-between">
                        <span>Access Key</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.keyPrefix}</code>
                      </div>
                    )}
                  </div>
                  <p className="text-[var(--color-text-muted)] mt-2 opacity-60">To refresh credentials:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded select-all mt-1">
                    ada credentials update --account={displayAccount} --role={displayRole} --provider=isengard
                  </code>
                </>
              ) : (
                <>
                  <p className="text-[var(--color-text-muted)] mb-1">Make sure VPN is connected, then run:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded select-all">
                    ada credentials update --account={displayAccount} --role={displayRole} --provider=isengard
                  </code>
                  {!hasRealValues && (
                    <p className="text-[var(--color-text-muted)] mt-1.5 opacity-50 text-[10px]">
                      Fill in Account ID and Role above — the command will update automatically.
                    </p>
                  )}
                </>
              );
            })() : (
              authHint?.awsProfiles && authHint.awsProfiles.length > 0 ? (
                <>
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                    <span className="text-green-400 font-medium">SSO profiles detected</span>
                  </div>
                  <div className="text-[var(--color-text-muted)] mb-2">
                    Profiles: {authHint.awsProfiles.map(p => (
                      <code key={p} className="text-[var(--color-text)] bg-[var(--color-bg)] px-1.5 py-0.5 rounded mr-1">{p}</code>
                    ))}
                  </div>
                  <p className="text-[var(--color-text-muted)] opacity-60">To refresh session:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded mt-1">
                    aws sso login --profile {authHint.awsProfiles[0]}
                  </code>
                </>
              ) : (
                <>
                  <p className="text-[var(--color-text-muted)] mb-1">
                    No AWS SSO profile found. Set one up first (one time), then sign in:
                  </p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded">
                    aws configure sso
                  </code>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded mt-1">
                    aws sso login --profile &lt;your-profile&gt;
                  </code>
                  <p className="text-[var(--color-text-muted)] mt-1.5 opacity-60 text-[10px]">
                    `aws configure sso` walks you through your Identity Center start URL + region.
                  </p>
                </>
              )
            )}
          </div>
        </div>
      )}

      {method === 'apikey' && (
        <div className="space-y-2">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Anthropic API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); setVerifyState('idle'); setVerifyResult(null); }}
              placeholder="sk-ant-..."
              autoComplete="off"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <p className="text-[10px] text-[var(--color-text-muted)] opacity-60">
            Stored securely on this device (not synced, not in config backups). Get a key at console.anthropic.com.
          </p>
        </div>
      )}

      {renderVerifySection()}

      {/* One-click deployment-context switch — for an internal employee on a
          machine that hasn't run ada/mwinit yet, or an external user misdetected
          as internal. Auto-detection is a default, not a lock (AC2). */}
      {mode === 'onboarding' && authHint?.runMode !== 'hive' && (
        <button
          onClick={toggleContext}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors underline decoration-dotted"
        >
          {context === 'external' ? 'Amazon employee? Switch to internal options' : 'Not internal? Switch to external options'}
        </button>
      )}
    </div>
  );
}
