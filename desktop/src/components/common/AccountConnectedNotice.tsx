/**
 * The one-time line an upgrader sees when their existing settings became the SwarmAI
 * AWS account (design docs/design/2026-10-31-swarmai-aws-account.md §6.4: "nothing pops
 * up; a one-time line: SwarmAI account connected: 533267412361"). Shown once per
 * account, as a toast with a link to Settings → SwarmAI Account; a new install (the
 * wizard connected it, the person already saw it) and an unverified record show nothing.
 *
 * Mounted with the app (AppRoutes' 'app' branch), never over the setup wizard: a new
 * install can derive a record from ~/.aws at startup, and the person connecting in the
 * wizard must not get this line. It uses only the toast context and the account API,
 * renders nothing and catches every failure in its effect (so no ErrorBoundary of its own).
 * A failed read, and a record not checked yet (the startup check runs after the record
 * is saved), are tried again a few times.
 */
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../../contexts/ToastContext';
import { awsAccountService } from '../../services/awsAccount';
import { OPEN_SETTINGS_EVENT } from './settingsEvents';

export const SEEN_KEY = 'swarmai.accountConnectedNotice';
const RETRY_MS = 20_000;
const TRIES = 6;

export default function AccountConnectedNotice() {
  const { t } = useTranslation();
  const { addToast } = useToast();
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = async (left: number) => {
      try {
        const v = await awsAccountService.get();
        const acct = v.account?.account_id;
        if (stop) return;
        if (v.state === 'unverified' && left > 1) {   // the startup check has not run yet
          timer = setTimeout(() => void attempt(left - 1), RETRY_MS);
          return;
        }
        if (!acct || !v.account?.derived || v.state !== 'connected') return;
        if (localStorage.getItem(SEEN_KEY) === acct) return;
        localStorage.setItem(SEEN_KEY, acct);
        addToast({
          severity: 'success', autoDismiss: false,
          message: t('awsConnect.connectedNotice', { account: acct }),
          action: {
            label: t('awsConnect.connectedNoticeOpen'),
            onClick: () => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'aws-account' } })),
          },
        });
      } catch {
        if (!stop && left > 1) timer = setTimeout(() => void attempt(left - 1), RETRY_MS);
      }
    };
    void attempt(TRIES);
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [addToast, t]);
  return null;
}
