/**
 * Global banner that surfaces a non-working credential state.
 *
 * HEALTH-POLL driven: reads `health.auth` from {@link useHealth}. Renders ONLY
 * when `auth === 'expired'` (definitive) — `valid` / `unknown` / undefined →
 * nothing (fail-open on transient/startup ambiguity).
 *
 * METHOD-AWARE (was hardcoded `mwinit -f`): the fix instruction matches the
 * user's actual auth method (fetched once from auth-hint), so an external SSO
 * user sees `aws sso login`, an Anthropic-direct user is pointed at Settings —
 * never an Amazon-internal command they can't run. Also offers a one-click
 * "Open Settings" deep-link to Settings → AI & Models so the fix is in-app.
 *
 * Note: in API-key (Anthropic-direct) mode the backend no longer runs the AWS
 * STS check, so `auth` is never 'expired' for those users — this banner is a
 * Bedrock-path (ada/sso/iam) concern in practice.
 */
import { useEffect, useState } from 'react';
import { useHealth } from '../../contexts/HealthContext';
import { useLayout } from '../../contexts/LayoutContext';
import { systemService } from '../../services/system';

type Method = 'ada' | 'sso' | 'apikey' | 'iam_role';

// Frontend mirror of backend auth_remediation.remediation_for — kept minimal.
function remediation(method: Method | undefined): { title: string; body: React.ReactNode } {
  switch (method) {
    case 'ada':
      return {
        title: 'AWS credentials need refreshing',
        body: <>Run <code className="px-1 py-0.5 rounded bg-[var(--color-bg)] font-mono">mwinit -f</code> then <code className="px-1 py-0.5 rounded bg-[var(--color-bg)] font-mono">ada credentials update</code>, or re-verify below.</>,
      };
    case 'sso':
      return {
        title: 'AWS SSO session expired',
        body: <>Run <code className="px-1 py-0.5 rounded bg-[var(--color-bg)] font-mono">aws sso login</code> in a terminal, then re-verify below.</>,
      };
    case 'iam_role':
      return {
        title: 'Instance role can\'t access Bedrock',
        body: <>Add <code className="px-1 py-0.5 rounded bg-[var(--color-bg)] font-mono">bedrock:InvokeModel</code> to the IAM role policy.</>,
      };
    case 'apikey':
      return {
        title: 'Anthropic API key not working',
        body: <>Update your API key in Settings → AI &amp; Models.</>,
      };
    default:
      return {
        title: 'Credentials aren\'t working',
        body: <>Check your authentication in Settings → AI &amp; Models.</>,
      };
  }
}

export default function CredentialBanner() {
  const { health } = useHealth();
  const { setSettingsTab } = useLayout();
  const [method, setMethod] = useState<Method | undefined>(undefined);

  // Fetch the active method once when the banner becomes relevant.
  useEffect(() => {
    if (health.auth !== 'expired') return;
    let cancelled = false;
    systemService.getAuthHint()
      .then((h) => { if (!cancelled) setMethod(h.suggestedMethod as Method); })
      .catch(() => { /* fall back to the generic remediation */ });
    return () => { cancelled = true; };
  }, [health.auth]);

  // Render ONLY on a definitive expired signal. valid/unknown/undefined → nothing.
  if (health.auth !== 'expired') return null;

  const { title, body } = remediation(method);

  return (
    <div
      className="fixed top-3 left-1/2 -translate-x-1/2 z-50 max-w-md px-4 py-2.5 rounded-lg shadow-lg bg-[var(--color-card)] border border-[var(--color-error,#ef4444)] text-sm"
      role="alert"
      aria-live="assertive"
    >
      <div className="flex items-start gap-2">
        <span className="text-[var(--color-error,#ef4444)] font-medium shrink-0">{title}</span>
        <span className="text-[var(--color-text-muted)]">{body}</span>
      </div>
      <div className="mt-2 flex justify-end">
        <button
          onClick={() => setSettingsTab('ai-models')}
          className="px-2.5 py-1 text-xs rounded bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80"
        >
          Open Settings
        </button>
      </div>
    </div>
  );
}
