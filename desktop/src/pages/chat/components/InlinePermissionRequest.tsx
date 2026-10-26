/**
 * Inline permission request — renders dangerous command approval UI
 * directly in the chat stream (not a modal popup).
 *
 * Follows the same inline pattern as AskUserQuestion: appears as a
 * content block within the assistant message, scoped to the tab/session, in the one
 * frame every card that waits on the user uses (ActCard; design
 * 2026-10-26-turn-cards-unified.md §1 D): "Swarm needs your approval", the timeout in
 * the header, Deny / Approve on the right. Every other state is one grey line that
 * keeps the command and what happened to it.
 *
 * States:
 * 1. Pending — command + reason + Deny/Approve buttons + countdown
 * 2. Approved — collapsed, green "Approved" badge
 * 3. Denied — collapsed, red "Denied" badge
 * 4. Expired — collapsed, muted "Timed out" badge. Display only: the card never
 *    sends a decision on its own. The backend owns the timeout (4 h,
 *    PERMISSION_ANSWER_TIMEOUT_SECONDS) and answers the tool call with a distinct
 *    "审批超时" reason, so an unanswered prompt never reads as a user denial.
 * 5. No longer pending — `decision: 'expired'` from the backend (core/permission_prompt:
 *    no hook waits on it any more — the turn stopped, the process restarted, it
 *    timed out): muted, "No longer pending".
 * 6. Not live in this tab — undecided, but this tab is not offering it (a stored
 *    prompt the 15s mirror has not re-surfaced yet): muted "Awaiting decision",
 *    never Deny/Approve buttons that would do nothing.
 *
 * @exports InlinePermissionRequest
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import { ActDone, ActFrame } from '../../../components/common/ActCard';

/** Display timeout in ms — mirrors backend PERMISSION_ANSWER_TIMEOUT_SECONDS (4 h).
 *  Counted from when this card mounted, so it can only run out AFTER the backend's. */
export const TIMEOUT_MS = 14_400_000;
/** Show countdown when remaining time <= this value (ms). */
const COUNTDOWN_THRESHOLD_MS = 300_000;

interface InlinePermissionRequestProps {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  /** Whether this is the active pending permission (buttons enabled). */
  isPending: boolean;
  /** Pre-existing decision (from content block update after user acts), or
   *  'expired' when the backend says no hook waits on it any more. */
  decision?: 'approve' | 'deny' | 'expired';
  onDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  /** Read-only render (History preview): a still-undecided historical request
   *  must NOT show live-looking Deny/Approve buttons or a running countdown —
   *  render a muted "awaiting decision" shell instead. */
  readOnly?: boolean;
}

export function InlinePermissionRequest({
  requestId,
  toolName: _toolName,
  toolInput,
  reason,
  isPending,
  decision,
  onDecision,
  readOnly,
}: InlinePermissionRequestProps) {
  const { t } = useTranslation();
  const [remainingMs, setRemainingMs] = useState(TIMEOUT_MS);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [expired, setExpired] = useState(false);
  const [localDecision, setLocalDecision] = useState<'approve' | 'deny' | null>(null);

  const gone = decision === 'expired';
  const effectiveDecision = gone ? null : (decision || localDecision);
  const command = (toolInput?.command as string) || JSON.stringify(toolInput, null, 2);

  // Countdown timer — only when pending. Running out only marks the card expired;
  // it never calls onDecision (the backend answers the call with its own timeout).
  useEffect(() => {
    if (!isPending || effectiveDecision || expired) return;

    const startTime = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, TIMEOUT_MS - elapsed);
      setRemainingMs(remaining);

      if (remaining <= 0) {
        if (timerRef.current) clearInterval(timerRef.current);
        setExpired(true);
      }
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isPending, effectiveDecision, expired]);

  const handleApprove = useCallback(() => {
    if (!isPending || effectiveDecision || expired) return;
    if (timerRef.current) clearInterval(timerRef.current);
    setLocalDecision('approve');
    onDecision?.(requestId, 'approve');
  }, [isPending, effectiveDecision, expired, requestId, onDecision]);

  const handleDeny = useCallback(() => {
    if (!isPending || effectiveDecision || expired) return;
    if (timerRef.current) clearInterval(timerRef.current);
    setLocalDecision('deny');
    onDecision?.(requestId, 'deny');
  }, [isPending, effectiveDecision, expired, requestId, onDecision]);

  const showCountdown = isPending && !effectiveDecision && remainingMs <= COUNTDOWN_THRESHOLD_MS && remainingMs > 0;
  const remainingSec = Math.ceil(remainingMs / 1000);
  const formatRemaining = (sec: number) => {
    if (sec >= 3600) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
    if (sec >= 60) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    return `${sec}s`;
  };

  // Decided: one grey line that keeps the decision (the command and what was decided)
  if (effectiveDecision) {
    const isApproved = effectiveDecision === 'approve';
    return (
      <ActDone testId="permission-done" icon={isApproved ? 'check' : 'close'}
        label={isApproved ? tf(t, 'chat.permission.approved', 'Approved') : tf(t, 'chat.permission.denied', 'Denied')}>
        <code title={command} className="font-mono text-[12px] text-[var(--color-text)]">{short(command)}</code>
      </ActDone>
    );
  }

  // Read-only (History preview) with no decision recorded, expired (the display
  // countdown ran out: no decision was sent from here), no longer pending, or not live
  // in this tab: the same grey line, never live-looking Deny/Approve buttons.
  if (readOnly || expired || gone || !isPending) {
    return (
      <ActDone testId="permission-done" icon={expired ? 'timer_off' : 'shield'}
        label={expired
          ? tf(t, 'chat.permission.timedOut', 'Timed out')
          : gone
            ? tf(t, 'chat.permission.noLongerPending', 'No longer pending')
            : tf(t, 'chat.permission.awaiting', 'Awaiting decision')}>
        <code title={command} className="font-mono text-[12px] text-[var(--color-text)]">{short(command)}</code>
      </ActDone>
    );
  }

  // Waiting: the one frame (ActCard), the timeout in the header, Deny / Approve right
  return (
    <ActFrame testId="permission-request" icon="shield" title={tf(t, 'chat.permission.title', 'Swarm needs your approval')}
      meta={showCountdown
        ? formatRemaining(remainingSec)
        : `${tf(t, 'chat.permission.timesOutIn', 'Times out in')} ${formatRemaining(remainingSec)}`}
      footer={(
        <>
          <button type="button" onClick={handleDeny} className="btn-sm btn-secondary">
            {tf(t, 'chat.permission.deny', 'Deny')}
          </button>
          <button type="button" onClick={handleApprove} className="btn-sm btn-primary">
            {tf(t, 'chat.permission.approve', 'Approve')}
          </button>
        </>
      )}>
      <p className="text-[12.5px] text-[var(--color-text-secondary)]">{reason}</p>
      <div className="rounded-[7px] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-[12px]">
        <span className="select-none text-[var(--color-text-dim)]">$ </span>
        <code className="break-all whitespace-pre-wrap text-[var(--color-text)]">{command}</code>
      </div>
    </ActFrame>
  );
}

/** A long command cut for the grey line; the full one is its tooltip. */
const short = (cmd: string) => (cmd.length > 80 ? `${cmd.slice(0, 80)}…` : cmd);
