/**
 * Permission request modal with timeout and countdown.
 *
 * Displays a command permission request to the user for approval or denial.
 * Includes a 5-minute auto-deny timeout with a visible countdown indicator
 * when ≤ 60 seconds remain.
 *
 * Key exports:
 * - `PermissionRequestModal` — modal component
 *
 * Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import type { PermissionRequest } from '../../types';
import { useToast } from '../../contexts/ToastContext';
import { useHealth } from '../../contexts/HealthContext';

/** Total timeout in milliseconds (5 minutes). */
const TIMEOUT_MS = 300_000;
/** Show countdown when remaining time ≤ this value (ms). */
const COUNTDOWN_THRESHOLD_MS = 60_000;

interface Props {
  request: PermissionRequest;
  onDecision: (decision: 'approve' | 'deny', feedback?: string) => void;
  isLoading?: boolean;
}

export function PermissionRequestModal({ request, onDecision, isLoading }: Props) {
  const [feedback, setFeedback] = useState('');
  const { addToast } = useToast();
  const { health } = useHealth();
  const [remainingMs, setRemainingMs] = useState(TIMEOUT_MS);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoDeniedRef = useRef(false);

  // Start countdown timer on mount
  useEffect(() => {
    const startTime = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, TIMEOUT_MS - elapsed);
      setRemainingMs(remaining);

      if (remaining <= 0 && !autoDeniedRef.current) {
        autoDeniedRef.current = true;
        if (timerRef.current) clearInterval(timerRef.current);
        onDecision('deny', 'Auto-denied: permission request timed out');
        addToast({
          severity: 'info',
          message: 'Permission request timed out and was automatically denied.',
          autoDismiss: true,
        });
      }
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [onDecision, addToast]);

  // Dismiss if backend is disconnected (stale request).
  // Guard with autoDeniedRef to prevent double-fire if timeout already denied.
  useEffect(() => {
    if (health.status === 'disconnected' && !autoDeniedRef.current) {
      autoDeniedRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
      onDecision('deny', 'Backend disconnected — request dismissed');
      addToast({
        severity: 'info',
        message: 'Permission request dismissed — backend is no longer available.',
        autoDismiss: true,
      });
    }
  }, [health.status, onDecision, addToast]);

  const showCountdown = remainingMs <= COUNTDOWN_THRESHOLD_MS && remainingMs > 0;
  const remainingSec = Math.ceil(remainingMs / 1000);

  const handleApprove = useCallback(() => {
    if (!isLoading) {
      onDecision('approve', feedback || undefined);
    }
  }, [isLoading, onDecision, feedback]);

  const handleDeny = useCallback(() => {
    if (!isLoading) {
      onDecision('deny', feedback || undefined);
    }
  }, [isLoading, onDecision, feedback]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-[var(--color-card)] rounded-lg shadow-xl max-w-lg w-full mx-4 border border-[var(--color-border)]">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
          <span className="material-symbols-outlined text-yellow-500 text-2xl">warning</span>
          <h3 className="text-lg font-semibold text-[var(--color-text)]">Permission Required</h3>
          {showCountdown && (
            <span className="ml-auto text-sm text-yellow-400 font-mono">
              {remainingSec}s
            </span>
          )}
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Reason */}
          <div>
            <p className="text-[var(--color-text-muted)] text-sm mb-2">
              A command requires your approval before execution:
            </p>
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3">
              <p className="text-yellow-200 font-medium">{request.reason}</p>
            </div>
          </div>

          {/* Command Details */}
          <div className="bg-[var(--color-bg)] rounded-lg p-4 font-mono text-sm">
            <div className="flex items-start gap-2 mb-2">
              <span className="text-[var(--color-text-muted)] shrink-0">Tool:</span>
              <span className="text-[var(--color-text)]">{request.toolName}</span>
            </div>
            <div className="flex items-start gap-2">
              <span className="text-[var(--color-text-muted)] shrink-0">Command:</span>
              <code className="text-red-400 break-all whitespace-pre-wrap">
                {request.toolInput.command as string || JSON.stringify(request.toolInput, null, 2)}
              </code>
            </div>
          </div>

          {/* Warning */}
          <div className="flex items-start gap-2 text-sm text-[var(--color-text-muted)]">
            <span className="material-symbols-outlined text-yellow-500 text-base shrink-0 mt-0.5">info</span>
            <p>
              This command has been flagged as potentially dangerous.
              Please review carefully before approving.
            </p>
          </div>

          {/* Feedback (optional) */}
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1.5">
              Feedback (optional)
            </label>
            <textarea
              placeholder="Add any notes about your decision..."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] text-sm resize-none focus:outline-none focus:border-primary"
              rows={2}
            />
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 justify-end p-4 border-t border-[var(--color-border)]">
          <button
            onClick={handleDeny}
            disabled={isLoading}
            className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-[var(--color-text)] rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            {isLoading ? (
              <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
            ) : (
              <span className="material-symbols-outlined text-sm">close</span>
            )}
            Deny
          </button>
          <button
            onClick={handleApprove}
            disabled={isLoading}
            className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-[var(--color-text)] rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            {isLoading ? (
              <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
            ) : (
              <span className="material-symbols-outlined text-sm">check</span>
            )}
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
