/**
 * NeedsYouRow — one Need You item as a two-action row, shared by the Welcome card
 * and the Need You overlay (design welcome-screen-v2.md, "Needs you rows").
 *
 * Same two actions as a reply's Next steps chips (NextSteps.tsx):
 *   - click the text → `onFill(prompt)`: the prompt goes into the chat input,
 *     the user can edit it, nothing is sent;
 *   - click ↑        → `onSend(prompt)`: the prompt is sent now.
 * `prompt` is the backend's `dispatch.message`, written in the user's voice with
 * the item's context and ids; the row shows the backend's plain `title` (what is
 * needed) and `summary` (which thing it is) as-is, never rewritten here.
 *
 * A Swarm Out item (`dispatch.context.kind` = "swarm_out": a summary of the runs and
 * questions waiting) is not a prompt: either action opens the Swarm Out tab, where each
 * run is handled (design 2026-10-31-swarm-out-run-review §3.7), then `onOpened` (the
 * overlay closes over the tab). A SwarmAI account item (`kind` = "aws_account": sign in
 * again) opens its settings page the same way (design 2026-10-31-swarmai-aws-account §7.1);
 * Settings is itself the overlay, so it replaces this one and `onOpened` is not called:
 * closing after it would close Settings in the same tick.
 *
 * @exports NeedsYouRow, needsYouPrompt, itemOpener, runOpener
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { AttentionEntry } from '../../services/attention';
import { getOverlayCtxBridge } from './overlayRegistry';
import { OPEN_SETTINGS_EVENT, openSwarmOutSettings } from '../common/settingsEvents';

/** True for an item that opens the Swarm Out tab instead of filling the chat. */
export function opensSwarmOut(it: AttentionEntry): boolean {
  return it.dispatch?.context?.kind === 'swarm_out';
}

/** The App's settings tab an item opens instead of filling the chat (the SwarmAI
 *  account's sign-in: the person acts on the page), else null. The Hive count opens the
 *  Swarm Out tab's settings instead (``opensSwarmOut``, ``view`` settings). */
export function opensSettingsTab(it: AttentionEntry): string | null {
  const ctx = it.dispatch?.context;
  return ctx?.kind === 'aws_account' && typeof ctx.settings_tab === 'string' ? ctx.settings_tab : null;
}

/** The text a row fills or sends: the backend prompt, else its title. */
export function needsYouPrompt(it: AttentionEntry): string {
  return it.dispatch?.message || it.title;
}

export interface NeedsYouRowProps {
  item: AttentionEntry;
  onFill: (prompt: string) => void;
  onSend: (prompt: string) => void;
  /** A leading mark (the Welcome card's hexagon); none in the overlay. */
  lead?: ReactNode;
  /** Draw the divider above this row (every row but the first in a card). */
  divider?: boolean;
  /** After an item opened the Swarm Out tab (the overlay closes itself); never after an
   *  opener that replaces the overlay (Settings). */
  onOpened?: () => void;
  testid?: string;
}

/** Where an item is handled instead of in chat, and how to open it; null for an ordinary
 *  prompt. `replacesOverlay`: the opener opens another overlay (Settings), which takes the
 *  place of the one the item is shown in, so the caller must not close afterwards (that
 *  would close Settings in the same tick); the Swarm Out tab is a chat tab under the
 *  overlay, so the caller closes. A Swarm Out item whose tab cannot be reached (no chat
 *  page published it) is an ordinary prompt. */
export interface ItemOpener { kind: 'settings' | 'swarmOut'; open: () => void; replacesOverlay: boolean }

export function itemOpener(it: AttentionEntry): ItemOpener | null {
  const settingsTab = opensSettingsTab(it);
  if (settingsTab) {
    return { kind: 'settings', replacesOverlay: true,
      open: () => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: settingsTab } })) };
  }
  // an item about Hives opens the Swarm Out tab on its settings view, where they are set up
  const bridgeShow = getOverlayCtxBridge().showSwarmOut;
  if (!opensSwarmOut(it) || !bridgeShow) return null;
  return { kind: 'swarmOut', replacesOverlay: false, open: it.dispatch?.context?.view === 'settings' ? openSwarmOutSettings : bridgeShow };
}

/** Run an item's opener, then `onOpened` (close the overlay) unless the opener already put
 *  another overlay in its place. */
export function runOpener(opener: ItemOpener, onOpened?: () => void): void {
  opener.open();
  if (!opener.replacesOverlay) onOpened?.();
}

export function NeedsYouRow({ item, onFill, onSend, lead, divider = false, testid = 'needs-you-row', onOpened }: NeedsYouRowProps) {
  const { t } = useTranslation();
  const prompt = needsYouPrompt(item);
  const opener = itemOpener(item);
  const settingsTab = opener?.kind === 'settings';
  const swarmOut = !!opener;
  const openSwarmOut = () => { if (opener) runOpener(opener, onOpened); };
  return (
    <div
      data-testid={testid}
      className={`group flex items-center gap-1 pr-2 ${divider ? 'border-t border-[var(--color-border)]' : ''}`}
    >
      <button
        type="button"
        data-testid={`${testid}-fill`}
        onClick={() => (swarmOut ? openSwarmOut() : onFill(prompt))}
        title={settingsTab ? t('needYou.openSettings') : swarmOut ? t('needYou.openSwarmOut') : t('needYou.fillHint')}
        className="flex-1 min-w-0 w-full text-left flex items-start gap-2.5 pl-4 pr-2 py-2 hover:bg-[var(--color-hover)] transition-colors cursor-pointer"
      >
        {lead && <span className="mt-[4px] shrink-0">{lead}</span>}
        <span className="flex-1 min-w-0">
          <span className="block truncate text-body font-semibold text-[var(--color-text)]">{item.title}</span>
          {item.summary && (
            <span className="block truncate text-small text-[var(--color-text-secondary)]">{item.summary}</span>
          )}
        </span>
        <span className="shrink-0 mt-[2px] text-meta text-[var(--color-text-muted)] px-[7px] py-px rounded-md bg-[var(--color-hover)]">
          {t(`needYou.source.${item.source}`, { defaultValue: item.source })}
        </span>
      </button>
      {!swarmOut && <button
        type="button"
        data-testid={`${testid}-send`}
        onClick={() => onSend(prompt)}
        title={t('needYou.sendNow')}
        aria-label={t('needYou.sendNowLabel', { title: item.title })}
        className="icon-btn-lg shrink-0 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-brand-strong)] hover:bg-[color-mix(in_srgb,var(--color-brand)_16%,transparent)] transition-colors"
      >
        <span className="material-symbols-outlined icon-md">arrow_upward</span>
      </button>}
    </div>
  );
}
