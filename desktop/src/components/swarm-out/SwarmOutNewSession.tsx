/**
 * SwarmOutNewSession — the one way to give the Swarm Out tab a new session (the
 * Swarm Out welcome / new-session design, ③④). It sits at the input box's top
 * right, on the Swarm Out tab only.
 *
 * - Not shown while the session has no content: there is nothing to leave.
 * - While the agent is replying, or a question or a confirmation card waits on the
 *   person, it is dashed and cannot be pressed; hovering says why (a new session
 *   would cut a dispatch off halfway).
 * - When the session is long (the context-usage warning level), it is highlighted:
 *   the same button, never a second one.
 *
 * Pressing it changes the session at once, without a confirmation (the old session
 * stays in history). Nothing else ever changes this tab's session: not a tab
 * switch, not a timer, not the backend. The two other ways are also the person's own
 * acts on that session: "Start fresh session" on a session whose recovery is
 * exhausted, and deleting the session from History.
 *
 * Background agents still running after a turn do not block it: they keep running in
 * the old session, which stays reachable from History and from the notice the press
 * leaves. Messages queued for the running turn do: the reset would drop them.
 */
import { useTranslation } from 'react-i18next';

export type NewSessionBlock = 'replying' | 'waiting' | null;

/** Why a new session cannot start now, from the tab's own state. */
export function newSessionBlock(tab: {
  isStreaming?: boolean; pendingQuestion?: unknown; pendingPermissionRequestId?: string | null;
  _serverQueue?: readonly unknown[]; _queuedLocal?: { size: number };
} | undefined): NewSessionBlock {
  if (!tab) return null;
  if (tab.isStreaming || (tab._serverQueue?.length ?? 0) > 0 || (tab._queuedLocal?.size ?? 0) > 0) return 'replying';
  if (tab.pendingQuestion || tab.pendingPermissionRequestId) return 'waiting';
  return null;
}

export function SwarmOutNewSession({ hasContent, block, long, onStart }: {
  hasContent: boolean; block: NewSessionBlock; long: boolean; onStart: () => void;
}) {
  const { t } = useTranslation();
  if (!hasContent) return null;
  const off = block !== null;
  const why = block === 'replying' ? t('swarmOut.fresh.busyReplying') : block === 'waiting' ? t('swarmOut.fresh.busyWaiting') : undefined;
  return (
    <button type="button" data-testid="swarm-out-new-session" data-long={long || undefined}
      aria-disabled={off} title={why ?? t('swarmOut.fresh.hint')}
      onClick={() => { if (!off) onStart(); }}
      className={`btn-md border transition-colors ${
        off ? 'border-dashed border-[var(--color-border)] text-[var(--color-text-dim)] cursor-not-allowed'
          : long ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_10%,transparent)] hover:bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)]'
            : 'border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)]'}`}>
      <span className="material-symbols-outlined icon-sm" aria-hidden>add</span>
      {t('swarmOut.fresh.button')}
    </button>
  );
}
