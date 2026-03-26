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
  const [method, setMethod] = useState<AuthMethod>('sso');
  const [region, setRegion] = useState('us-east-1');
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
        setMethod(hint.suggestedMethod);
        // Pre-fill ada fields from real credentials
        if (hint.adaDetails) {
          if (hint.adaDetails.accountId) setAdaAccount(hint.adaDetails.accountId);
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

  const methods: { id: AuthMethod; label: string; desc: string }[] = [
    { id: 'sso', label: 'Bedrock (SSO)', desc: 'AWS SSO / IdC' },
    { id: 'ada', label: 'Bedrock (ADA)', desc: 'Amazon Internal' },
    { id: 'apikey', label: 'API Key', desc: 'Anthropic Direct' },
  ];

  return (
    <div className="space-y-4">
      {/* Auth method cards */}
      <div className="grid grid-cols-3 gap-3">
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
          <Dropdown
            label="AWS Region"
            options={AWS_REGION_OPTIONS}
            selectedId={region}
            onChange={setRegion}
            placeholder="Select region..."
          />

          {/* ADA-specific fields */}
          {method === 'ada' && (
            <>
              <div>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">ADA Account ID</label>
                <input
                  type="text"
                  value={adaAccount}
                  onChange={(e) => setAdaAccount(e.target.value)}
                  placeholder="123456789012"
                  className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
              <div>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">ADA Role</label>
                <input
                  type="text"
                  value={adaRole}
                  onChange={(e) => setAdaRole(e.target.value)}
                  placeholder="Admin"
                  className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
            </>
          )}

          {/* Credential status / setup hint */}
          <div className="p-3 bg-[var(--color-card)] rounded-lg text-xs">
            {method === 'ada' ? (
              authHint?.adaDetails?.configured ? (
                <>
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                    <span className="text-green-400 font-medium">ADA credentials active</span>
                  </div>
                  <div className="space-y-1 text-[var(--color-text-muted)]">
                    {authHint.adaDetails.accountId && (
                      <div className="flex justify-between">
                        <span>Account</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.accountId}</code>
                      </div>
                    )}
                    {authHint.adaDetails.roleName && (
                      <div className="flex justify-between">
                        <span>Role</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.roleName}</code>
                      </div>
                    )}
                    {authHint.adaDetails.keyPrefix && (
                      <div className="flex justify-between">
                        <span>Access Key</span>
                        <code className="text-[var(--color-text)]">{authHint.adaDetails.keyPrefix}</code>
                      </div>
                    )}
                  </div>
                  <p className="text-[var(--color-text-muted)] mt-2 opacity-60">To refresh credentials:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded select-all mt-1">
                    ada credentials update --account={adaAccount || 'ACCOUNT'} --role={adaRole || 'ROLE'} --provider=isengard
                  </code>
                </>
              ) : (
                <>
                  <p className="text-[var(--color-text-muted)] mb-1">Make sure VPN is connected, then run:</p>
                  <code className="block font-mono text-[var(--color-text)] bg-[var(--color-bg)] p-2 rounded select-all">
                    ada credentials update --account={adaAccount || 'ACCOUNT'} --role={adaRole || 'ROLE'} --provider=isengard
                  </code>
                </>
              )
            ) : (
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

      {/* Verify button */}
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

      {/* Result */}
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
    </div>
  );
}
