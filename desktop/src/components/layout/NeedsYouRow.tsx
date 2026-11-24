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
 * run is handled (design 2026-10-31-swarm-out-run-review §3.7), then `onOpened`.
 * A SwarmAI account item (`kind` = "aws_account": sign in again, Hives short) opens its
 * settings page the same way (design 2026-10-31-swarmai-aws-account §7.1).
 *
 * @exports NeedsYouRow, needsYouPrompt, itemOpener
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
  /** After a Swarm Out item opened the Swarm Out tab (the overlay closes itself). */
  onOpened?: () => void;
  testid?: string;
}

/** Where an item is handled instead of in chat — the App's settings page or the Swarm Out
 *  tab — and how to open it; null for an ordinary prompt. A Swarm Out item whose tab
 *  cannot be reached (no chat page published it) is an ordinary prompt. */
export function itemOpener(it: AttentionEntry): { kind: 'settings' | 'swarmOut'; open: () => void } | null {
  const settingsTab = opensSettingsTab(it);
  if (settingsTab) {
    return { kind: 'settings', open: () => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: settingsTab } })) };
  }
  // an item about Hives opens the Swarm Out tab on its settings view, where they are set up
  const bridgeShow = getOverlayCtxBridge().showSwarmOut;
  if (!opensSwarmOut(it) || !bridgeShow) return null;
  return { kind: 'swarmOut', open: it.dispatch?.context?.view === 'settings' ? openSwarmOutSettings : bridgeShow };
}

export function NeedsYouRow({ item, onFill, onSend, lead, divider = false, testid = 'needs-you-row', onOpened }: NeedsYouRowProps) {
  const { t } = useTranslation();
  const prompt = needsYouPrompt(item);
  const opener = itemOpener(item);
  const settingsTab = opener?.kind === 'settings';
  const swarmOut = !!opener;
  const openSwarmOut = () => { opener?.open(); onOpened?.(); };
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
