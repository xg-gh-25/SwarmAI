/**
 * Structured chat error message component.
 *
 * Renders SSE error events in the chat area with a red accent left border,
 * visually distinct from normal assistant messages. Displays error code,
 * message, detail, and an optional `suggestedAction` as a highlighted
 * element. Supports error-code-specific behaviors:
 *
 * - `AGENT_TIMEOUT`        — "Retry" button re-sends last user message
 * - `SDK_SUBPROCESS_TIMEOUT` — "Retry" button re-sends last user message
 * - `RATE_LIMIT_EXCEEDED`  — countdown timer, auto-re-enables on expiry
 * - `SERVICE_UNAVAILABLE`  — triggers immediate health check
 *
 * Key exports:
 * - `ChatErrorMessage`     — React component
 * - `ChatErrorMessageProps` — prop interface
 *
 * Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5
 */

import { useEffect, useState } from 'react';
import { useHealth } from '../../contexts/HealthContext';

export interface ChatErrorMessageProps {
  error: {
    code?: string;
    message?: string;
    detail?: string;
    suggestedAction?: string;
    retryAfter?: number;
  };
  onRetry?: () => void;
}

export function ChatErrorMessage({ error, onRetry }: ChatErrorMessageProps) {
  const { triggerHealthCheck } = useHealth();

  // Task 13.2: SERVICE_UNAVAILABLE triggers immediate health check
  useEffect(() => {
    if (error.code === 'SERVICE_UNAVAILABLE') {
      triggerHealthCheck();
    }
  }, [error.code, triggerHealthCheck]);

  return (
    <div
      className="border-l-4 border-red-500 bg-red-500/10 rounded-r-lg p-4 my-2"
      role="alert"
    >
      {/* Error header */}
      <div className="flex items-center gap-2 mb-1">
        <span className="material-symbols-outlined text-red-400 text-lg">
          {error.code === 'SDK_SUBPROCESS_TIMEOUT' || error.code === 'AGENT_TIMEOUT'
            ? 'schedule' : 'error'}
        </span>
        <span className="text-red-400 font-semibold text-sm">
          {error.code === 'SDK_SUBPROCESS_TIMEOUT'
            ? 'AI Service Timeout'
            : error.code === 'AGENT_TIMEOUT'
              ? 'Response Timeout'
              : error.code === 'RATE_LIMIT_EXCEEDED'
                ? 'Rate Limited'
                : error.code === 'SERVICE_UNAVAILABLE'
                  ? 'Service Unavailable'
                  : error.code === 'CREDENTIALS_EXPIRED'
                    ? 'Credentials Expired'
                    : error.code ?? 'Error'}
        </span>
      </div>

      {/* Error message */}
      <p className="text-[var(--color-text)] text-sm mb-1">
        {error.message ?? 'An error occurred'}
      </p>

      {/* Detail */}
      {error.detail && (
        <p className="text-[var(--color-text-muted)] text-xs mb-2">
          {error.detail}
        </p>
      )}

      {/* Suggested action */}
      {error.suggestedAction && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded px-3 py-2 text-yellow-200 text-sm mb-2">
          💡 {error.suggestedAction}
        </div>
      )}

      {/* Task 13.2: Error-code-specific behaviors */}
      {(error.code === 'AGENT_TIMEOUT' || error.code === 'SDK_SUBPROCESS_TIMEOUT') && onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary-hover text-white text-sm rounded-lg transition-colors mt-1"
        >
          <span className="material-symbols-outlined text-sm">refresh</span>
          Retry
        </button>
      )}

      {error.code === 'RATE_LIMIT_EXCEEDED' && error.retryAfter && (
        <RateLimitCountdownInline retryAfterSec={error.retryAfter} />
      )}
    </div>
  );
}

/**
 * Inline countdown for RATE_LIMIT_EXCEEDED errors.
 * Self-contained timer that decrements every second.
 */
function RateLimitCountdownInline({ retryAfterSec }: { retryAfterSec: number }) {
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
  }, [retryAfterSec]); // restart timer when retryAfterSec changes

  if (remaining <= 0) {
    return (
      <p className="text-green-400 text-sm mt-1">
        ✓ Rate limit expired — you may resume.
      </p>
    );
  }

  return (
    <p className="text-yellow-400 text-sm mt-1">
      ⏳ Rate limited — resuming in {remaining}s
    </p>
  );
}
