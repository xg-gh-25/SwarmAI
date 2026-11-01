/**
 * Structured chat error message component.
 *
 * Renders SSE error events in the chat area with a visually distinct
 * background and red accent left border, clearly separated from normal
 * assistant messages. Displays error code, message, detail, and an
 * optional `suggestedAction` as a highlighted tip.
 *
 * Error-code-specific behaviors:
 * - `AGENT_TIMEOUT`         — informational (user can send a message to resume)
 * - `SDK_SUBPROCESS_TIMEOUT` — informational (user can send a message to resume)
 * - `RATE_LIMIT_EXCEEDED`   — countdown timer, auto-re-enables on expiry
 * - `SERVICE_UNAVAILABLE`   — triggers immediate health check
 * - `CREDENTIALS_EXPIRED`   — a "fix the sign-in" button: the chat's sign-in line
 *                             (CredentialBanner) verifies and runs its one action
 *                             (the pre-flight's expiry, or a sign-in error mid-turn)
 * - `ACCOUNT_NOT_CONNECTED` — nothing was ever connected: said as it is (not as an
 *                             expiry), with a button to the account page
 * - any other code (an interrupted turn, a timeout, a failed call) — a Continue button
 *   when `onContinue` is given (the last message only): the turn stopped early, so
 *   this is where asking it to go on belongs. A card with its own action above
 *   (sign-in, connect, retry, rate-limit countdown, service down) gets none.
 *
 * Key exports:
 * - `ChatErrorMessage`      — React component
 * - `ChatErrorMessageProps`  — prop interface
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useHealth } from '../../contexts/HealthContext';
import { fixSignIn, openSettings } from '../common/settingsEvents';
import { ASSISTANT_NAME } from '../../pages/chat/assistantName';

export interface ChatErrorMessageProps {
  error: {
    code?: string;
    message?: string;
    detail?: string;
    suggestedAction?: string;
    retryAfter?: number;
  };
  /** Callback for QUEUE_TIMEOUT retry — re-sends the saved message when a slot opens. */
  onRetry?: () => void;
  /** Ask the agent to continue (sends "continue"); shown unless the card has its own action. */
  onContinue?: () => void;
}

/** Codes whose card already carries the one action that fixes it: no Continue. */
const OWN_ACTION_CODES = new Set([
  'CREDENTIALS_EXPIRED', 'ACCOUNT_NOT_CONNECTED', 'QUEUE_TIMEOUT', 'RATE_LIMIT_EXCEEDED',
  'SERVICE_UNAVAILABLE',
]);

export function ChatErrorMessage({ error, onRetry, onContinue }: ChatErrorMessageProps) {
  const { t } = useTranslation();
  const { triggerHealthCheck } = useHealth();

  // SERVICE_UNAVAILABLE triggers immediate health check
  useEffect(() => {
    if (error.code === 'SERVICE_UNAVAILABLE') {
      triggerHealthCheck();
    }
  }, [error.code, triggerHealthCheck]);

  return (
    <div
      className={`border-l-4 rounded-r-lg p-4 my-2 ${
        // Informational errors (timeouts, queue) get amber styling — less alarming.
        // Hard errors (rate limit, credentials, service down) get red.
        error.code === 'SDK_SUBPROCESS_TIMEOUT' || error.code === 'AGENT_TIMEOUT' || error.code === 'QUEUE_TIMEOUT' || error.code === 'ACCOUNT_NOT_CONNECTED' || !error.code
          ? 'border-amber-500 bg-amber-950/30'
          : 'border-red-500 bg-red-950/40'
      }`}
      role="alert"
    >
      {/* Error header */}
      <div className="flex items-center gap-2 mb-1">
        <span className={`material-symbols-outlined icon-sm ${
          error.code === 'SDK_SUBPROCESS_TIMEOUT' || error.code === 'AGENT_TIMEOUT' || error.code === 'QUEUE_TIMEOUT' || error.code === 'ACCOUNT_NOT_CONNECTED' || !error.code
            ? 'text-amber-400'
            : 'text-red-400'
        }`}>
          {error.code === 'SDK_SUBPROCESS_TIMEOUT' || error.code === 'AGENT_TIMEOUT'
            ? 'schedule'
            : error.code === 'QUEUE_TIMEOUT'
              ? 'hourglass_top'
              : !error.code
                ? 'sync_problem'
                : 'error'}
        </span>
        <span className={`font-semibold text-sm ${
          error.code === 'SDK_SUBPROCESS_TIMEOUT' || error.code === 'AGENT_TIMEOUT' || error.code === 'QUEUE_TIMEOUT' || error.code === 'ACCOUNT_NOT_CONNECTED' || !error.code
            ? 'text-amber-400'
            : 'text-red-400'
        }`}>
          {error.code === 'SDK_SUBPROCESS_TIMEOUT'
            ? t('ui.ChatErrorMessage.aiServiceTimeout')
            : error.code === 'AGENT_TIMEOUT'
              ? t('ui.ChatErrorMessage.responseTimeout')
              : error.code === 'RATE_LIMIT_EXCEEDED'
                ? t('ui.ChatErrorMessage.rateLimited')
                : error.code === 'SERVICE_UNAVAILABLE'
                  ? t('ui.ChatErrorMessage.serviceUnavailable')
                  : error.code === 'CREDENTIALS_EXPIRED'
                    ? t('credentialBanner.chat.credentialsExpired')
                    : error.code === 'ACCOUNT_NOT_CONNECTED'
                    ? t('credentialBanner.chat.notConnected')
                    : error.code === 'QUEUE_TIMEOUT'
                      ? t('ui.ChatErrorMessage.slotsBusy')
                      : error.code ?? t('ui.ChatErrorMessage.interrupted')}
        </span>
      </div>

      {/* Error message (never connected: one fixed sentence, so it is translated here) */}
      <p className="text-[var(--color-text)] text-sm mb-1">
        {error.code === 'ACCOUNT_NOT_CONNECTED'
          ? t('credentialBanner.remedy.not_connected.message')
          : (error.message ?? t('ui.ChatErrorMessage.errorOccurred'))}
      </p>

      {/* Detail */}
      {error.detail && (
        <p className="text-[var(--color-text-muted)] text-xs mb-2">
          {error.detail}
        </p>
      )}

      {/* Suggested action */}
      {error.suggestedAction && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded px-3 py-2 text-yellow-200 text-sm">
          💡 {error.code === 'ACCOUNT_NOT_CONNECTED' ? t('credentialBanner.remedy.not_connected.fix') : error.suggestedAction}
        </div>
      )}

      {error.code === 'RATE_LIMIT_EXCEEDED' && error.retryAfter && (
        <RateLimitCountdownInline retryAfterSec={error.retryAfter} />
      )}

      {error.code === 'CREDENTIALS_EXPIRED' && (
        <button type="button" onClick={fixSignIn} data-testid="chat-error-fix-sign-in"
          className="mt-3 btn-md btn-primary transition-colors">
          {t('credentialBanner.fixFromChat')}
        </button>
      )}
      {error.code === 'ACCOUNT_NOT_CONNECTED' && (
        <button type="button" onClick={() => openSettings('aws-account')} data-testid="chat-error-connect"
          className="mt-3 btn-md btn-primary transition-colors">
          {t('credentialBanner.chat.connect')}
        </button>
      )}

      {error.code === 'QUEUE_TIMEOUT' && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 btn-md btn-primary transition-colors"
        >
          {t('ui.ChatErrorMessage.retryWhenSlotOpens')}
        </button>
      )}
      {onContinue && !(error.code && OWN_ACTION_CODES.has(error.code)) && (
        <button type="button" onClick={onContinue} data-testid="continue-button"
          aria-label={t('chat.message.continueAria', 'Continue response')}
          className="mt-3 btn-md btn-secondary transition-colors"
          title={t('chat.message.continueTitle', { name: ASSISTANT_NAME, defaultValue: 'Continue — ask {{name}} to continue its response' })}>
          <span className="material-symbols-outlined icon-xs">play_arrow</span>
          {t('chat.message.continue', 'Continue')}
        </button>
      )}
    </div>
  );
}

/**
 * Inline countdown for RATE_LIMIT_EXCEEDED errors.
 * Self-contained timer that decrements every second.
 */
function RateLimitCountdownInline({ retryAfterSec }: { retryAfterSec: number }) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(retryAfterSec);

  // Reset remaining when retryAfterSec prop changes (e.g. new rate limit)
  useEffect(() => {
    setRemaining(retryAfterSec);
  }, [retryAfterSec]);

  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setInterval(() => {
      setRemaining((prev: number) => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [retryAfterSec]); // eslint-disable-line react-hooks/exhaustive-deps -- remaining omitted to avoid restarting interval

  if (remaining <= 0) {
    return (
      <p className="text-green-400 text-sm mt-2">
        {t('ui.ChatErrorMessage.rateLimitExpired')}
      </p>
    );
  }

  return (
    <p className="text-yellow-400 text-sm mt-2">
      {t('ui.ChatErrorMessage.resumingIn', { seconds: remaining })}
    </p>
  );
}
