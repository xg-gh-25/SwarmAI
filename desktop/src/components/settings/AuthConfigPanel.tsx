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

type AuthMethod = 'access_keys' | 'sso' | 'ada' | 'apikey';

interface AuthConfigPanelProps {
  mode: 'onboarding' | 'settings';
  onVerifySuccess?: () => void;
}

const AWS_REGION_OPTIONS = [
  { id: 'us-east-1', name: 'US East (N. Virginia)', description: 'us-east-1' },
  { id: 'us-west-2', name: 'US West (Oregon)', description: 'us-west-2' },
  { id: 'eu-west-1', name: 'EU (Ireland)', description: 'eu-west-1' },
  { id: 'eu-central-1', name: 'EU (Frankfurt)', description: 'eu-central-1' },
  { id: 'ap-northeast-1', name: 'Asia Pacific (Tokyo)', description: 'ap-northeast-1' },
  { id: 'ap-southeast-1', name: 'Asia Pacific (Singapore)', description: 'ap-southeast-1' },
];

export default function AuthConfigPanel({ mode, onVerifySuccess }: AuthConfigPanelProps) {
  const [method, setMethod] = useState<AuthMethod>('access_keys');
  const [region, setRegion] = useState('us-east-1');
  const [accountId, setAccountId] = useState('');
  const [accessKeyId, setAccessKeyId] = useState('');
  const [secretAccessKey, setSecretAccessKey] = useState('');
  const [adaAccount, setAdaAccount] = useState('');
  const [adaRole, setAdaRole] = useState('');
  const [verifyState, setVerifyState] = useState<'idle' | 'verifying' | 'success' | 'error'>('idle');
  const [verifyResult, setVerifyResult] = useState<VerifyAuthResponse | null>(null);
  const [authHint, setAuthHint] = useState<AuthHintResponse | null>(null);

  // Auto-detect best auth method and load real credential details
  useEffect(() => {
    systemService.getAuthHint()
      .then((hint) => {
        setAuthHint(hint);
        // Map backend suggestion to UI method
        const methodMap: Record<string, AuthMethod> = {
          'ada': 'ada',
          'sso': 'sso',
          'apikey': 'apikey',
          'iam_role': 'access_keys',  // Hive mode uses instance role, show as access_keys
        };
        setMethod(methodMap[hint.suggestedMethod] || 'access_keys');
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
      .catch(() => { /* default access_keys is fine */ });
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
      // Save auth config before verifying
      const isBedrock = method !== 'apikey';
      const configUpdate: Record<string, unknown> = {
        use_bedrock: isBedrock,
        aws_region: region,
      };
      if (method === 'ada') {
        configUpdate.ada_account = adaAccount;
        configUpdate.ada_role = adaRole;
      }
      await settingsService.updateAPIConfiguration(configUpdate);

      const result = await systemService.verifyAuth();
      setVerifyResult(result);
      setVerifyState(result.success ? 'success' : 'error');

      if (result.success && onVerifySuccess) {
        onVerifySuccess();
      }
    } catch (e) {
      setVerifyResult({
        success: false,
        error: String(e),
        errorType: 'unknown',
        fixHint: 'Check your network connection and try again.',
      });
      setVerifyState('error');
    }
  };

  // Build methods list — show Ada only when detected (Amazon internal)
  const hasAda = authHint?.hasAdaDir ?? false;
  const methods: { id: AuthMethod; label: string; desc: string }[] = [
    { id: 'access_keys', label: 'Access Keys', desc: 'IAM credentials' },
    { id: 'sso', label: 'AWS SSO', desc: 'Identity Center' },
    ...(hasAda ? [{ id: 'ada' as AuthMethod, label: 'Ada', desc: 'Amazon Internal' }] : []),
    { id: 'apikey', label: 'API Key', desc: 'Anthropic Direct' },
  ];

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
          {/* AWS Account ID — shown for all AWS methods */}
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">AWS Account ID</label>
            <input
              type="text"
              value={accountId}
              onChange={(e) => { setAccountId(e.target.value); if (method === 'ada') setAdaAccount(e.target.value); }}
              placeholder="Enter your 12-digit AWS account ID"
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>

          <Dropdown
            label="AWS Region"
            options={AWS_REGION_OPTIONS}
            selectedId={region}
            onChange={setRegion}
            placeholder="Select region..."
          />

          {/* Access Keys fields */}
          {method === 'access_keys' && (
            <>
              <div>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">Access Key ID</label>
                <input
                  type="text"
                  value={accessKeyId}
                  onChange={(e) => setAccessKeyId(e.target.value)}
                  placeholder="AKIA..."
                  className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
              <div>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">Secret Access Key</label>
                <input
                  type="password"
                  value={secretAccessKey}
                  onChange={(e) => setSecretAccessKey(e.target.value)}
                  placeholder="Enter secret access key"
                  className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/40 focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
            </>
          )}

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
                  <p className="text-[var(--color-text-muted)] mb-1">Authenticate with AWS SSO:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded">
                    aws sso login --profile your-profile
                  </code>
                </>
              )
            )}
          </div>
        </div>
      )}

      {method === 'apikey' && (
        <div className="p-3 bg-[var(--color-card)] rounded-lg text-xs">
          <p className="text-[var(--color-text-muted)] mb-1">
            Set the <code className="px-1 py-0.5 bg-[var(--color-bg)] rounded">ANTHROPIC_API_KEY</code> environment variable before launching SwarmAI:
          </p>
          <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded">
            export ANTHROPIC_API_KEY=sk-ant-...
          </code>
        </div>
      )}

      {renderVerifySection()}
    </div>
  );
}
