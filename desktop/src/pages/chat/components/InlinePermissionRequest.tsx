/**
 * Inline permission request — renders dangerous command approval UI
 * directly in the chat stream (not a modal popup).
 *
 * Follows the same inline pattern as AskUserQuestion: appears as a
 * content block within the assistant message, scoped to the tab/session.
 *
 * States:
 * 1. Pending — command + reason + Deny/Approve buttons + countdown
 * 2. Approved — collapsed, green "Approved" badge
 * 3. Denied — collapsed, red "Denied" badge
 * 4. Expired — collapsed, muted "Timed out" badge
 *
 * @exports InlinePermissionRequest
 */

import { useState, useEffect, useRef, useCallback } from 'react';

/** Total timeout in milliseconds (5 minutes — matches backend). */
const TIMEOUT_MS = 300_000;
/** Show countdown when remaining time <= this value (ms). */
const COUNTDOWN_THRESHOLD_MS = 60_000;

interface InlinePermissionRequestProps {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  /** Whether this is the active pending permission (buttons enabled). */
  isPending: boolean;
  /** Pre-existing decision (from content block update after user acts). */
  decision?: 'approve' | 'deny';
  onDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
}

export function InlinePermissionRequest({
  requestId,
  toolName: _toolName,
  toolInput,
  reason,
  isPending,
  decision,
  onDecision,
}: InlinePermissionRequestProps) {
  const [remainingMs, setRemainingMs] = useState(TIMEOUT_MS);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoDeniedRef = useRef(false);
  const [localDecision, setLocalDecision] = useState<'approve' | 'deny' | null>(null);

  const effectiveDecision = decision || localDecision;
  const command = (toolInput?.command as string) || JSON.stringify(toolInput, null, 2);

  // Countdown timer — only when pending
  useEffect(() => {
    if (!isPending || effectiveDecision) return;

    const startTime = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, TIMEOUT_MS - elapsed);
      setRemainingMs(remaining);

      if (remaining <= 0 && !autoDeniedRef.current) {
        autoDeniedRef.current = true;
        if (timerRef.current) clearInterval(timerRef.current);
        setLocalDecision('deny');
        onDecision?.(requestId, 'deny');
      }
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isPending, effectiveDecision, requestId, onDecision]);

  const handleApprove = useCallback(() => {
    if (!isPending || effectiveDecision) return;
    if (timerRef.current) clearInterval(timerRef.current);
    setLocalDecision('approve');
    onDecision?.(requestId, 'approve');
  }, [isPending, effectiveDecision, requestId, onDecision]);

  const handleDeny = useCallback(() => {
    if (!isPending || effectiveDecision) return;
    if (timerRef.current) clearInterval(timerRef.current);
    setLocalDecision('deny');
    onDecision?.(requestId, 'deny');
  }, [isPending, effectiveDecision, requestId, onDecision]);

  const showCountdown = isPending && !effectiveDecision && remainingMs <= COUNTDOWN_THRESHOLD_MS && remainingMs > 0;
  const remainingSec = Math.ceil(remainingMs / 1000);

  // Decided state — compact display
  if (effectiveDecision) {
    const isApproved = effectiveDecision === 'approve';
    return (
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-md my-1 ${
        isApproved
          ? 'bg-green-500/10 border border-green-500/20'
          : 'bg-red-500/10 border border-red-500/20'
      }`}>
        <span className={`material-symbols-outlined text-sm ${isApproved ? 'text-green-500' : 'text-red-500'}`}>
          {isApproved ? 'check_circle' : 'cancel'}
        </span>
        <span className="text-sm text-[var(--color-text-muted)]">
          <code className="text-xs bg-[var(--color-hover)] px-1 py-0.5 rounded">{command.length > 60 ? command.slice(0, 60) + '...' : command}</code>
        </span>
        <span className={`ml-auto text-xs font-medium ${isApproved ? 'text-green-500' : 'text-red-500'}`}>
          {isApproved ? 'Approved' : 'Denied'}
        </span>
      </div>
    );
  }

  // Pending state — full display with action buttons
  return (
    <div className="bg-[var(--color-card)] border border-amber-500/30 rounded-lg overflow-hidden my-2">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-amber-500/10 border-b border-amber-500/20">
        <span className="material-symbols-outlined text-amber-500 text-base">shield</span>
        <span className="text-sm font-medium text-[var(--color-text)]">Permission Required</span>
        {showCountdown && (
          <span className="ml-auto text-xs text-amber-400 font-mono tabular-nums">
            {remainingSec}s
          </span>
        )}
      </div>

      {/* Command */}
      <div className="px-3 py-2 space-y-2">
        <div className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="shrink-0 mt-0.5">Flagged:</span>
          <span className="text-amber-400">{reason}</span>
        </div>
        <div className="bg-[var(--color-bg)] rounded p-2 font-mono text-xs">
          <div className="flex items-start gap-2">
            <span className="text-[var(--color-text-muted)] shrink-0 select-none">$</span>
            <code className="text-red-400 break-all whitespace-pre-wrap">{command}</code>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-[var(--color-border)]">
        <button
          onClick={handleDeny}
          className="flex items-center gap-1.5 px-3 py-1 text-xs font-medium text-red-400 bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 rounded-md transition-colors"
        >
          <span className="material-symbols-outlined text-sm">close</span>
          Deny
        </button>
        <button
          onClick={handleApprove}
          className="flex items-center gap-1.5 px-3 py-1 text-xs font-medium text-green-400 bg-green-500/10 hover:bg-green-500/20 border border-green-500/20 rounded-md transition-colors"
        >
          <span className="material-symbols-outlined text-sm">check</span>
          Approve
        </button>
        <span className="ml-auto text-[10px] text-[var(--color-text-muted)]">
          Auto-deny in {Math.ceil(remainingMs / 1000)}s
        </span>
      </div>
    </div>
  );
}
