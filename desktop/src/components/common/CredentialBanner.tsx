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
 * PLACEMENT: this banner is mounted at the APP ROOT (all modes, ungated) so it
 * can surface expiry during onboarding/loading too. It therefore must NOT depend
 * on LayoutContext (which only exists inside ThreeColumnLayout) — calling
 * useLayout() here threw "useLayout must be used within a LayoutProvider" and
 * crashed the whole app at boot (regression from the method-aware rework). The
 * "Open Settings" deep-link is dispatched as a `swarm:open-settings` window event
 * that the app shell (ThreeColumnLayoutInner) listens for; when the shell isn't
 * mounted yet (onboarding), the click is a harmless no-op (no settings modal to
 * open there anyway).
 *
 * Note: in API-key (Anthropic-direct) mode the backend no longer runs the AWS
 * STS check, so `auth` is never 'expired' for those users — this banner is a
 * Bedrock-path (ada/sso/iam) concern in practice.
 */
import { useEffect, useState } from 'react';
import { useHealth } from '../../contexts/HealthContext';
import { systemService } from '../../services/system';

// Deep-link event: the app shell (ThreeColumnLayoutInner) listens for this and
// opens Settings on the given tab. Decouples this root-mounted banner from
// LayoutContext (which only exists inside the app shell) — see file docstring.
export const OPEN_SETTINGS_EVENT = 'swarm:open-settings';

type Method = 'ada' | 'sso' | 'apikey' | 'iam_role' | 'bedrock_api_key';

// Frontend mirror of backend auth_remediation.remediation_for — kept minimal.
// `configured` = whether ANY credential signal was detected. When false, the
// user never set creds up (NoCredentialsError → auth='expired' too), so the
// copy must say CONFIGURE, not "expired/refresh" for a session they never had (F2).
function remediation(
  method: Method | undefined,
  configured: boolean,
): { title: string; body: React.ReactNode } {
  if (!configured) {
    return {
      title: 'No credentials configured',
      body: <>Set up authentication in Settings → AI &amp; Models to get started.</>,
    };
  }
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
    case 'bedrock_api_key':
      return {
        title: 'Bedrock API key not working',
        body: <>Your bearer token expired (max 12h). Generate a new one and enter it in Settings → AI &amp; Models.</>,
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
  const [method, setMethod] = useState<Method | undefined>(undefined);
  // undefined until the hint resolves; then true iff ANY credential was detected.
  const [configured, setConfigured] = useState<boolean>(true);

  // Fetch the active method once when the banner becomes relevant.
  useEffect(() => {
    if (health.auth !== 'expired') return;
    let cancelled = false;
    systemService.getAuthHint()
      .then((h) => {
        if (cancelled) return;
        setMethod(h.suggestedMethod as Method);
        // No ada/sso/apikey signal → the user never configured creds (a
        // NoCredentialsError, not an expiry). Show "configure", not "refresh".
        // EXCEPT Hive/iam_role: has_ada_dir/has_sso_cache are forced false on
        // Hive but the IAM instance role IS a valid credential — it's always
        // "configured" (there's no in-app setup for it), so never show the
        // configure copy for it (Gate-2 HIGH regression fix).
        const isIamRole = h.suggestedMethod === 'iam_role' || h.runMode === 'hive';
        setConfigured(isIamRole || Boolean(h.hasAdaDir || h.hasSsoCache || h.hasApiKey));
      })
      .catch(() => { /* fall back to the generic remediation */ });
    return () => { cancelled = true; };
  }, [health.auth]);

  // Render ONLY on a definitive expired signal. valid/unknown/undefined → nothing.
  if (health.auth !== 'expired') return null;

  const { title, body } = remediation(method, configured);

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
          onClick={() => window.dispatchEvent(
            new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'ai-models' } }),
          )}
          className="px-2.5 py-1 text-xs rounded bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80"
        >
          Open Settings
        </button>
      </div>
    </div>
  );
}
